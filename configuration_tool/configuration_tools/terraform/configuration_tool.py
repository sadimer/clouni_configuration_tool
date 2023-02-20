import copy
import logging
import os

import hcl2
import json
import six
import yaml

from configuration_tool.common import utils

from configuration_tool.configuration_tools.common.configuration_tool import ConfigurationTool
from configuration_tool.common.tosca_reserved_keys import ANSIBLE, TERRAFORM, IMPLEMENTATION, RELATIONSHIPS, NODES, \
    DEPLOY_PATH, TYPE, CHECKSUM, CHECKSUM_ALGORITHM, INPUTS, NAME, ATTRIBUTES, ID
from configuration_tool.configuration_tools.common.instance_model.instance_model import \
    get_actual_state_of_instance_model

TERRAFORM_RESERVED_KEYS = \
    (PROJECT_PATH, STATE, FORCE_INIT, RESOURCE) = ('project_path', 'state', 'force_init', 'resource')
ANSIBLE_RESERVED_KEYS = \
    (REGISTER, PATH, FILE, STATE, LINEINFILE, SET_FACT, IS_DEFINED, IS_UNDEFINED,
     IMPORT_TASKS_MODULE, COPY, SRC, DST, STAT, FAIL, MSG, WHEN, WITH_LIST, ASYNC) = \
    ('register', 'path', 'file', 'state', 'lineinfile', 'set_fact', ' is defined',
     ' is undefined', 'include', 'copy', 'src', 'dest', 'stat', 'fail', 'msg', 'when', 'with_list', 'async')


class TerraformConfigurationTool(ConfigurationTool):
    TOOL_NAME = TERRAFORM

    def __init__(self, provider=None):
        super(TerraformConfigurationTool, self).__init__(provider=provider)

    def get_from_interface(self, element_object, target_directory, is_delete, operation, cluster_name,
                           additional_args=None):
        if additional_args is None:
            additional_args = {}
        else:
            additional_args_global = copy.deepcopy(additional_args.get('global', {}))
            additional_args_element = copy.deepcopy(additional_args.get(element_object.name, {}))
            additional_args = utils.deep_update_dict(additional_args_global,
                                                     additional_args_element)
        ansible_tasks = []
        scripts = []
        timeout = None

        for interface_name, interface in self.get_interfaces_from_node(element_object).items():
            interface_operation = interface.get(operation, {})
            if isinstance(interface_operation, six.string_types):
                implementations = interface_operation
            else:
                implementations = interface_operation.get(IMPLEMENTATION)
            (_, element_type, _) = utils.tosca_type_parse(element_object.type)
            if (interface_name == 'Standard' and element_type == NODES or interface_name == 'Configure' and
                element_type == RELATIONSHIPS) and implementations is not None:
                if isinstance(implementations, six.string_types):
                    implementations = [implementations]
                if isinstance(implementations, dict):
                    if 'timeout' in implementations:
                        if isinstance(implementations['timeout'], int) or isinstance(implementations['timeout'],
                                                                                     six.string_types):
                            timeout = int(implementations['timeout'])
                        else:
                            logging.error('Timeout must be a string or integer value')
                            raise Exception('Timeout must be a string or integer value')
                    if 'operation_host' in implementations:
                        if isinstance(implementations['operation_host'], six.string_types):
                            host = self._resolve_tosca_travers([implementations['operation_host'], ''],
                                                               element_object.name)
                            for v in self.operations_graph:
                                if v.name == host[0] and v.operation == 'create':
                                    element_object.host = v.host  # if operation_host defined - we try to find
                                    # operation create of target node and get host of this operation
                                    break
                        else:
                            logging.error('Operation_host must be a string value')
                            raise Exception('Operation_host must be a string value')
                    if 'dependencies' in implementations:
                        if isinstance(implementations['dependencies'], list):
                            for dependency in implementations['dependencies']:
                                if isinstance(dependency, dict) and TYPE in dependency and FILE in dependency:
                                    if dependency.get(DEPLOY_PATH):
                                        ansible_tasks.append({
                                            COPY: {
                                                SRC: os.path.join(utils.get_tmp_clouni_dir(), target_directory,
                                                                  dependency[FILE]),
                                                DST: dependency[DEPLOY_PATH]
                                            }
                                        })
                                    if dependency.get(CHECKSUM) and dependency.get(CHECKSUM_ALGORITHM):
                                        ansible_tasks.append({
                                            STAT: {
                                                CHECKSUM_ALGORITHM: dependency[CHECKSUM_ALGORITHM],
                                                'get_checksum': 'yes',
                                                PATH: os.path.join(utils.get_tmp_clouni_dir(), target_directory,
                                                                   dependency[FILE])
                                            },
                                            REGISTER: CHECKSUM
                                        })
                                        ansible_tasks.append({
                                            'debug': {
                                                MSG: self.rap_ansible_variable(CHECKSUM)
                                            }
                                        })
                                        ansible_tasks.append({
                                            FAIL: {
                                                MSG: 'Checksum of %s file is incorrect' % dependency[FILE]
                                            },
                                            WHEN: CHECKSUM + '.' + STAT + '.' + CHECKSUM + ' != '
                                                  + '"' + str(dependency[CHECKSUM]) + '"'
                                        })
                        else:
                            logging.error('Dependencies interface implementation must be a list')
                            raise Exception('Dependencies interface implementation must be a list')
                    if 'primary' in implementations:
                        if isinstance(implementations['primary'], six.string_types):
                            implementations = [implementations['primary']]
                        else:
                            logging.error('Primary interface implementation must be a string')
                            raise Exception('Primary interface implementation must be a string')
                scripts.extend(implementations)
                if interface_operation.get(INPUTS) is not None:
                    for input_name, input_value in interface_operation[INPUTS].items():
                        ansible_tasks.append({
                            SET_FACT: {
                                input_name: input_value
                            }
                        })
                for script in implementations:
                    script_filename = os.path.join(os.path.join(utils.get_tmp_clouni_dir(), 'artifacts'), script)
                    if timeout:
                        new_ansible_task = {
                            ASYNC: timeout,
                            IMPORT_TASKS_MODULE: os.path.join(utils.get_tmp_clouni_dir(), script_filename)
                        }
                    else:
                        new_ansible_task = {
                            IMPORT_TASKS_MODULE: os.path.join(utils.get_tmp_clouni_dir(), script_filename)
                        }
                    for task in ansible_tasks:
                        task.update(additional_args)
                    ansible_tasks.append(new_ansible_task)
        return ansible_tasks

    def rap_ansible_variable(self, s):
        r = "{{ " + s + " }}"
        return r

    def get_for_create(self, element_object, target_directory, node_filter_config, description_by_type,
                       module_by_type, additional_args=None):
        """
        Fulfill the dict with ansible task arguments to create infrastructure
        If the node contains get_operation_output parameters then the operation is executed
        If the operation is not mentioned then it is not executed
        Operations are mentioned in the node or in relationship_template
        :param: node: ProviderResource
        :param additional_args: dict of arguments to add
        :return: string of ansible task to place in playbook
        """

        if additional_args is None:
            additional_args = {}
        else:
            additional_args_global = copy.deepcopy(additional_args.get('global', {}))
            additional_args_element = copy.deepcopy(additional_args.get(element_object.name, {}))
            additional_args = utils.deep_update_dict(additional_args_global,
                                                     additional_args_element)

        ansible_tasks = []

        configuration_args = {}
        for arg_key, arg in element_object.configuration_args.items():
            configuration_args[arg_key] = arg

        task_name = element_object.name.replace('-', '_')

        terraform_config = {
            RESOURCE: {
                module_by_type: {
                    task_name: configuration_args
                }
            }
        }
        print(json.dumps(terraform_config, sort_keys=True, indent=4))

        ansible_task_as_dict = dict()
        ansible_task_as_dict[NAME] = description_by_type
        ansible_task_as_dict['shell'] = '|\n' + 'cat > main.tf.json << EOF\n' \
                                        + json.dumps(terraform_config, sort_keys=True, indent=4) + '\nEOF'
        ansible_tasks.append(copy.deepcopy(ansible_task_as_dict))
        del ansible_task_as_dict['shell']

        ansible_task_as_dict[NAME] = description_by_type
        ansible_task_as_dict[self.TOOL_NAME] = {
            PROJECT_PATH: task_name,
            STATE: 'present',
            FORCE_INIT: True
        }
        ansible_task_as_dict[REGISTER] = task_name
        ansible_task_as_dict.update(additional_args)
        ansible_tasks.append(copy.deepcopy(ansible_task_as_dict))
        return ansible_tasks
