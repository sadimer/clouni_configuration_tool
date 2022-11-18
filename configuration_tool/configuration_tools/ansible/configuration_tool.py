import json
import time
from queue import Queue

from graphlib import TopologicalSorter
from toscaparser.functions import GetAttribute, Concat, Token, GetProperty, GetInput

from configuration_tool.common import utils
from configuration_tool.common.tosca_reserved_keys import PARAMETERS, VALUE, EXTRA, SOURCE, INPUTS, NODE_FILTER, NAME, \
    NODES, GET_OPERATION_OUTPUT, IMPLEMENTATION, ANSIBLE, GET_INPUT, RELATIONSHIPS, ATTRIBUTES, GET_ATTRIBUTE, CONCAT, \
    JOIN, TOKEN, REQUIREMENTS, NODE, CAPABILITIES, DEFAULT, GET_PROPERTY, PROPERTIES
from configuration_tool.configuration_tools.ansible.instance_model.instance_model import update_instance_model, \
    get_actual_state_of_instance_model

from configuration_tool.providers.common.provider_configuration import ProviderConfiguration
from configuration_tool.configuration_tools.common.configuration_tool import ConfigurationTool, \
    OUTPUT_IDS, OUTPUT_ID_RANGE_START, OUTPUT_ID_RANGE_END

from configuration_tool.configuration_tools.ansible.runner.runner import grpc_cotea_run_ansible, run_ansible

import copy, yaml, os, itertools, six, logging

SEPARATOR = '.'

ARTIFACT_RANGE_START = 1000
ARTIFACT_RANGE_END = 9999

ANSIBLE_RESERVED_KEYS = \
    (REGISTER, PATH, FILE, STATE, LINEINFILE, SET_FACT, IS_DEFINED, IS_UNDEFINED, IMPORT_TASKS_MODULE) = \
    ('register', 'path', 'file', 'state', 'lineinfile', 'set_fact', ' is defined', ' is undefined', 'include')

REQUIRED_CONFIG_PARAMS = (INITIAL_ARTIFACTS_DIRECTORY, DEFAULT_HOST) = ("initial_artifacts_directory", "default_host")


class AnsibleConfigurationTool(ConfigurationTool):
    TOOL_NAME = ANSIBLE
    """
    Must be tested by TestAnsibleOpenstack.test_translating_to_ansible
    """

    def __init__(self, provider=None):
        super(AnsibleConfigurationTool, self).__init__()

        self.provider = provider
        main_config = self.tool_config.get_section('main')
        for param in REQUIRED_CONFIG_PARAMS:
            if not param in main_config.keys():
                logging.error("Configuration parameter \'%s\' is missing in Ansible configuration" % param)
                raise Exception("Configuration parameter \'%s\' is missing in Ansible configuration" % param)

        for param in REQUIRED_CONFIG_PARAMS:
            setattr(self, param, main_config[param])

    def to_dsl(self, provider, operations_graph, reversed_operations_graph, cluster_name, is_delete,
               target_directory=None, extra=None, debug=False, grpc_cotea_endpoint=None):

        provider_config = ProviderConfiguration(self.provider)
        ansible_config = provider_config.get_section(ANSIBLE)
        node_filter_config = provider_config.get_subsection(ANSIBLE, NODE_FILTER)

        ids_file_path = utils.get_tmp_clouni_dir() + 'id_vars_' + cluster_name + self.get_artifact_extension()

        if not debug and not grpc_cotea_endpoint:
            logging.warning("No grpc cotea endpoint provided! Use debug mode!")
            debug = True
        # the graph of operations at the moment is a dictionary of copies of ProviderTemplatre objects,
        # of the form Node/Relationship: {the set of opers of Nodes/Relationships on which it depends}

        self.operations_graph = operations_graph
        self.cluster_name = cluster_name

        elements = TopologicalSorter(operations_graph)
        # use TopologicalSorter for creating graph

        if is_delete:
            elements = TopologicalSorter(reversed_operations_graph)

        elements.prepare()
        # first operations from on top of the graph in state 'ready'

        ansible_playbook = []
        q = Queue()
        # queue for node names + operations
        active = []
        # list of parallel active operations
        first = True

        while elements.is_active():
            node_attributes = None
            # try to get new finished operation from queue and find it in list of active
            # if get - mark done this operation (but before it find in graph)
            # if ready operations exists - get it and execute, remove from active
            try:
                node_attributes = q.get_nowait()
            except Exception:
                time.sleep(1)
            if isinstance(node_attributes, Exception):
                logging.error("Deploy failed with %s" % node_attributes)
                raise Exception("Deploy failed with %s" % node_attributes)
            if node_attributes is not None:
                if isinstance(node_attributes, dict) and len(node_attributes) == 1:
                    node_name = list(node_attributes.keys())[0]
                    for node in active:
                        if node.name == node_name.split(SEPARATOR)[0] and node.operation == node_name.split(SEPARATOR)[1]:
                            active.remove(node)
                            elements.done(node)
                            update_instance_model(self.cluster_name, node.tmpl, node.type, node.name, node_attributes[node_name], is_delete)
                else:
                    logging.error('Bad element in queue')
                    raise Exception('Bad element in queue')
            for v in elements.get_ready():
                # in delete mode we skip all operations exept delete and create operation transforms to delete
                if is_delete:
                    if v.operation == 'create':
                        v.operation = 'delete'
                    else:
                        elements.done(v)
                        continue
                logging.debug("Creating ansible play from operation: %s" % v.name + ':' + v.operation)
                extra_tasks_for_delete = self.get_extra_tasks_for_delete(v.type, v.name.replace('-', '_'),
                                                                         ids_file_path)
                description_prefix, module_prefix = self.get_module_prefixes(is_delete, ansible_config)
                description_by_type = self.ansible_description_by_type(v.type_name, description_prefix)
                module_by_type = self.ansible_module_by_type(v.type_name, module_prefix)
                ansible_tasks = []
                if not is_delete:
                    result = self.resolve_get_attribute_and_intrinsic_functions({v.name: v.tmpl})
                    v.tmpl = result[v.name]
                host = self.default_host
                # reload id_vars file
                if not is_delete and first and not debug:
                    first = False
                    first_tasks = []
                    first_tasks.append(copy.deepcopy({FILE: {
                        PATH: ids_file_path,
                        STATE: 'absent'}}))
                    first_tasks.append(copy.deepcopy({FILE: {
                        PATH: ids_file_path,
                        STATE: 'touch'}}))
                    run_ansible(first_tasks, grpc_cotea_endpoint, {}, {}, 'localhost')
                # create playbook for every operation
                if v.operation == 'delete':
                    if not v.is_software_component:
                        tasks = [copy.deepcopy({'include_vars': ids_file_path})]
                        tasks.extend(self.get_ansible_tasks_for_delete(v, description_by_type, module_by_type,
                                                                           additional_args=extra))
                        tasks.extend(
                            self.get_ansible_tasks_from_interface(v, target_directory, is_delete, v.operation,
                                                                      cluster_name,
                                                                      additional_args=extra))
                        if not any(item == module_by_type for item in
                                    ansible_config.get('modules_skipping_delete', [])):
                            ansible_tasks.extend(copy.deepcopy(tasks))
                    else:
                        host = 'localhost' # v.host
                        ansible_tasks.extend(copy.deepcopy(
                            self.get_ansible_tasks_from_interface(v, target_directory, is_delete, v.operation,
                                                                  cluster_name,
                                                                  additional_args=extra)))
                elif v.operation == 'create':
                    if not v.is_software_component:
                        ansible_tasks.append(copy.deepcopy({'include_vars': ids_file_path}))
                        ansible_tasks.extend(self.get_ansible_tasks_for_create(v, target_directory, node_filter_config,
                                                                          description_by_type, module_by_type,
                                                                          additional_args=extra))
                        ansible_tasks.extend(
                            self.get_ansible_tasks_from_interface(v, target_directory, is_delete, v.operation,
                                                                  cluster_name,
                                                                  additional_args=extra))
                        ansible_tasks.extend(copy.deepcopy(extra_tasks_for_delete))

                    else:
                        host = 'localhost' # v.host
                        ansible_tasks.extend(copy.deepcopy(
                            self.get_ansible_tasks_from_interface(v, target_directory, is_delete, v.operation,
                                                                  cluster_name,
                                                                  additional_args=extra)))
                else:
                    (_, element_type, _) = utils.tosca_type_parse(v.type)
                    if element_type == NODES:
                        if v.is_software_component:
                            host = 'localhost' # v.host
                    # operations for relationships executes on target/source host depends on operation
                    elif element_type == RELATIONSHIPS:
                        if v.operation == 'pre_configure_target' or v.operation == 'post_configure_target' or v.operation == 'add_source':
                            for elem in operations_graph:
                                if elem.name == v.target:
                                    if elem.is_software_component:
                                        host = 'localhost' # v.host
                                    break
                        elif v.operation == 'pre_configure_source' or v.operation == 'post_configure_source':
                            for elem in operations_graph:
                                if elem.name == v.source:
                                    if elem.is_software_component:
                                        host = 'localhost' # v.host
                                    break
                        else:
                            logging.error("Unsupported operation for relationship in operation graph")
                            raise Exception("Unsupported operation for relationship in operation graph")
                    else:
                        logging.error("Unsupported element type in operation graph")
                        raise Exception("Unsupported element type in operation graph")
                    ansible_tasks.extend(copy.deepcopy(
                        self.get_ansible_tasks_from_interface(v, target_directory, is_delete, v.operation, cluster_name,
                                                              additional_args=extra)))
                if len(ansible_tasks) > 0:
                    ansible_play_for_elem = {'tasks': ansible_tasks,
                                             'hosts': host,
                                              'name': description_prefix + ' ' + self.provider + ' cluster: ' +
                                                      v.name + ':' + v.operation}
                    ansible_playbook.append(ansible_play_for_elem)
                if debug:
                    elements.done(v)
                else:
                    if len(ansible_tasks) > 0:
                        self.run(ansible_tasks, grpc_cotea_endpoint, host, v.name, v.operation, q, extra, ansible_config, self.get_defined_attributes(v))
                    else:
                        elements.done(v)
                    active.append(v)
        if is_delete:
            ansible_tasks = [{FILE: {
                PATH: ids_file_path,
                STATE: 'absent'}}]
            ansible_play_for_elem = {'tasks': ansible_tasks,
                                         'hosts': self.default_host,
                                         'name': 'Renew id_vars_example.yaml'}
            ansible_playbook.append(ansible_play_for_elem)
            if not debug:
                run_ansible(ansible_tasks, grpc_cotea_endpoint, {}, {}, self.default_host)
        return yaml.dump(ansible_playbook, default_flow_style=False)

    def replace_all_get_functions(self, data):
        if isinstance(data, dict):
            if len(data) == 1 and data.get(GET_OPERATION_OUTPUT, None) is not None:
                full_op_name = '_'.join(data[GET_OPERATION_OUTPUT][:3]).lower()
                output_id = self.global_operations_info[full_op_name][OUTPUT_IDS][data[GET_OPERATION_OUTPUT][-1]]
                return self.rap_ansible_variable(output_id)
            if len(data) == 1 and data.get(GET_INPUT, None) is not None:
                output_id = self.global_variables['input_' + data[GET_INPUT]]
                return self.rap_ansible_variable(output_id)

            r = {}
            for k, v in data.items():
                r[k] = self.replace_all_get_functions(v)
            return r

        elif isinstance(data, (list, set, tuple)):
            type_save = type(data)
            r = type_save()
            for i in data:
                temp = self.replace_all_get_functions(i)
                r = type_save(itertools.chain(r, [temp]))
            return r
        else:
            return data

    def get_ansible_tasks_for_create(self, element_object, target_directory, node_filter_config, description_by_type,
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

        ansible_args = copy.copy(element_object.configuration_args)
        ansible_args[STATE] = 'present'
        task_name = element_object.name.replace('-', '_')
        ansible_task_as_dict = dict()
        ansible_task_as_dict[NAME] = description_by_type
        ansible_task_as_dict[module_by_type] = configuration_args
        ansible_task_as_dict[REGISTER] = task_name
        ansible_task_as_dict.update(additional_args)
        ansible_tasks.append(ansible_task_as_dict)
        return ansible_tasks

    def get_ansible_tasks_for_delete(self, element_object, description_by_type, module_by_type, additional_args=None):
        """
        Fulfill the dict with ansible task arguments to delete infrastructure
        Operations are mentioned in the node or in relationship_template
        :param: node: ProviderResource
        :return: string of ansible task to place in playbook
        """
        ansible_tasks = []
        if additional_args is None:
            additional_args = {}
        else:
            additional_args_global = copy.deepcopy(additional_args.get('global', {}))
            additional_args_element = {}
            additional_args = utils.deep_update_dict(additional_args_global, additional_args_element)

        task_name = element_object.name.replace('-', '_')
        ansible_task_list = [dict(), dict(), dict()]
        for task in ansible_task_list:
            task[NAME] = description_by_type
        ansible_task_list[0][module_by_type] = {
            NAME: self.rap_ansible_variable(task_name + '_delete'), 'state': 'absent'}
        ansible_task_list[1][module_by_type] = {
            NAME: self.rap_ansible_variable('item'), 'state': 'absent'}
        ansible_task_list[2][module_by_type] = self.rap_ansible_variable('item')
        ansible_task_list[2]['loop'] = self.rap_ansible_variable(task_name + '_dicts')
        ansible_task_list[0]['when'] = task_name + '_delete' + IS_DEFINED
        ansible_task_list[1]['when'] = task_name + '_ids is defined'
        ansible_task_list[1]['loop'] = self.rap_ansible_variable(task_name + '_ids | flatten(levels=1)')
        ansible_task_list[2]['when'] = task_name + '_dicts' + IS_DEFINED
        for task in ansible_task_list:
            task[REGISTER] = task_name + '_var'
            task.update(additional_args)
            ansible_tasks.append(task)
            ansible_tasks.append(
                {SET_FACT: task_name + '=\'' + self.rap_ansible_variable(task_name + '_var') + '\'',
                 'when': task_name + '_var' + '.changed'})
        return ansible_tasks

    def get_ansible_tasks_from_interface(self, element_object, target_directory, is_delete, operation, cluster_name,
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

        for interface_name, interface in self.get_interfaces_from_node(element_object).items():
            interface_operation = interface.get(operation, {})
            if isinstance(interface_operation, six.string_types):
                implementations = interface_operation
            else:
                implementations = interface_operation.get(IMPLEMENTATION)
            (_, element_type, _) = utils.tosca_type_parse(element_object.type)
            if (
                    interface_name == 'Standard' and element_type == NODES or interface_name == 'Configure' and element_type == RELATIONSHIPS) and implementations is not None:
                if isinstance(implementations, six.string_types):
                    implementations = [implementations]
                if isinstance(implementations, dict) and 'primary' in implementations and isinstance(
                        implementations['primary'], six.string_types):
                    implementations = [implementations['primary']]
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
                    new_ansible_task = {
                        IMPORT_TASKS_MODULE: os.path.join(utils.get_tmp_clouni_dir(), script_filename)
                    }
                    for task in ansible_tasks:
                        task.update(additional_args)
                    ansible_tasks.append(new_ansible_task)
        return ansible_tasks

    def ansible_description_by_type(self, provider_source_obj_type, description_prefix):
        return description_prefix + ' ' + utils.snake_case(provider_source_obj_type).replace('_', ' ')

    def ansible_module_by_type(self, provider_source_obj_type, module_prefix):
        return module_prefix + utils.snake_case(provider_source_obj_type)

    def get_module_prefixes(self, is_delete, ansible_config=None, low=False):
        if is_delete:
            desc = 'Delete'
        else:
            desc = 'Create'
        module_prefix = ''
        if ansible_config:
            new_module_desc = ansible_config.get('module_description' + '_' + desc.lower())
            if new_module_desc:
                desc = new_module_desc
            new_module_prefix = ansible_config.get('module_prefix')
            if new_module_prefix:
                module_prefix = new_module_prefix
        if low:
            desc = desc.lower()
        return desc, module_prefix

    def get_ansible_artifacts_directory(self):
        return os.path.join(os.path.dirname(os.path.realpath(__file__)), self.initial_artifacts_directory)

    def rap_ansible_variable(self, s):
        r = "{{ " + s + " }}"
        return r

    @staticmethod
    def create_artifact_data(data):
        parameters = data[PARAMETERS]
        source = data[SOURCE]
        extra = data.get(EXTRA)
        value = data[VALUE]
        task_data = {
            source: parameters,
            REGISTER: value
        }
        tasks = [
            task_data
        ]
        if extra:
            task_data.update(extra)
        logging.debug("New artifact was created: \n%s" % yaml.dump(tasks))
        return tasks

    def create_artifact(self, filename, data):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        tasks = AnsibleConfigurationTool.create_artifact_data(data)
        with open(filename, "w") as f:
            filedata = yaml.dump(tasks, default_flow_style=False)
            f.write(filedata)
            logging.info("Artifact for executor %s was created: %s" % (self.TOOL_NAME, filename))

    def get_defined_attributes(self, provider_tpl):
        return list(provider_tpl.type_definition.get(ATTRIBUTES))

    def get_extra_tasks_for_delete(self, type, task_name, path):
        ansible_tasks_for_create = []
        (_, _, node_type) = utils.tosca_type_parse(type)
        node_type = utils.snake_case(node_type)
        # TODO: fix this chaos
        if type == 'openstack.nodes.FloatingIp':  # temporary solutions
            ansible_tasks_for_create.append({
                'set_fact': {task_name + '_dict': {'floating_ip_address': self.rap_ansible_variable(
                    'item.floating_ip.floating_ip_address'),
                    'server': self.rap_ansible_variable('item.floating_ip.port_details.device_id'),
                    'purge': 'yes',
                    'state': 'absent'}},
                'with_items': self.rap_ansible_variable(task_name + ".results"),
                'when': task_name + IS_DEFINED,
                'register': 'tmp'
            })
            ansible_tasks_for_create.append({
                'set_fact': {
                    task_name + '_dicts': self.rap_ansible_variable(
                        task_name + '_dicts | default([]) + [item.ansible_facts.' + task_name + '_dict]')
                },
                'with_items': self.rap_ansible_variable("tmp.results"),
                'when': task_name + '_dict' + IS_DEFINED
            })
            ansible_tasks_for_create.append({
                LINEINFILE: {
                    PATH: path,
                    'line': task_name + '_dicts:\n' + self.rap_ansible_variable(task_name + '_dicts | to_nice_yaml')},
                'when': task_name + '_dicts' + IS_DEFINED,
            })
        elif type == 'amazon.nodes.Key':  # temporary solutions
            # amazon ec2 has very BAD return values - there is no id in return value for ec2_key module
            ansible_tasks_for_create.append({
                LINEINFILE: {
                    PATH: path,
                    'line': '' + task_name + '_delete' + ': ' + self.rap_ansible_variable(
                        task_name + '.' + node_type + '.name')},
                'when': task_name + '.' + node_type + '.name' + IS_DEFINED
            })
        else:
            ansible_tasks_for_create.append({
                'set_fact': {
                    task_name + '_list': self.rap_ansible_variable(
                        task_name + '_list' + " | default([])") + " + [ \"{{ item.id }}\" ]"},
                'loop': self.rap_ansible_variable(task_name + '.results | flatten(levels=1) '),
                'when': 'item.id ' + IS_DEFINED
            })
            ansible_tasks_for_create.append({  # temporary solutions
                'set_fact': {
                    task_name + '_list': self.rap_ansible_variable(
                        task_name + '_list' + " | default([])") + " + [ \"{{ item." + node_type + "_ids[0] }}\" ]"},
                'loop': self.rap_ansible_variable(task_name + '.results | flatten(levels=1) '),
                'when': 'item.' + node_type + '_ids' + IS_DEFINED
            })
            ansible_tasks_for_create.append({
                'set_fact': {
                    task_name + '_list': {task_name + '_ids': self.rap_ansible_variable(task_name + '_list')}},
                'when': task_name + '_list' + IS_DEFINED
            })
            ansible_tasks_for_create.append({
                LINEINFILE: {
                    PATH: path,
                    'line': '' + task_name + '_delete' + ': ' + self.rap_ansible_variable(task_name + '.id')},
                'when': task_name + '.id' + IS_DEFINED
            })
            # amazon ec2 has very BAD return values - we need to search where is id of current node
            ansible_tasks_for_create.append({
                LINEINFILE: {
                    PATH: path,
                    'line': '' + task_name + '_delete' + ': ' + self.rap_ansible_variable(
                        task_name + '.' + node_type + '_id')},
                'when': task_name + '.' + node_type + '_id' + IS_DEFINED
            })
            for elem in node_type.split('_'):  # temporary solutions
                ansible_tasks_for_create.append({
                    LINEINFILE: {
                        PATH: path,
                        'line': '' + task_name + '_delete' + ': ' + self.rap_ansible_variable(
                            task_name + '.' + elem + '.id')},
                    'when': task_name + '.' + elem + '.id' + IS_DEFINED
                })
            ansible_tasks_for_create.append({
                LINEINFILE: {
                    PATH: path,
                    'line': self.rap_ansible_variable(task_name + '_list' + ' | to_nice_yaml')},
                'when': task_name + '_list' + IS_DEFINED
            })
            ansible_tasks_for_create.append({
                'fail': {'msg': 'Variable ' + task_name + ' is undefined! So it will not be deleted'},
                'when': task_name + '_list is undefined and ' + task_name + '.id is undefined',
                'ignore_errors': True})
        return ansible_tasks_for_create

    def get_artifact_extension(self):
        return '.yaml'

    def run(self, ansible_tasks, grpc_cotea_endpoint, hosts, name, op, q, extra, ansible_config, attributes):
        extra_env = {}
        extra_vars = extra.get('global')
        plugins_path = os.path.join(utils.get_tmp_clouni_dir(), 'ansible_plugins/plugins/modules/cloud/', self.provider)
        grpc_cotea_run_ansible(ansible_tasks, grpc_cotea_endpoint, extra_env, extra_vars, hosts, name, op, q, ansible_config,
                               ansible_library=plugins_path, attributes=attributes)
    
    def resolve_get_attribute_and_intrinsic_functions(self, data, tmpl_name=None):
        if isinstance(data, dict):
            new_data = {}
            for key, value in data.items():
                if key == GET_ATTRIBUTE:
                    new_data = self._get_attribute_value(value, tmpl_name)
                elif key == GET_INPUT:
                    new_data = self._get_input_value(value, tmpl_name)
                elif key == GET_PROPERTY:
                    new_data = self._get_property_value(value, tmpl_name)
                elif key == CONCAT:
                    if isinstance(value, list):
                        new_data = ""
                        for elem in value:
                            if isinstance(elem, (six.string_types, int, float)):
                                new_data += str(elem)
                            else:
                                new_data += str(self.resolve_get_attribute_and_intrinsic_functions(elem, tmpl_name))
                    else:
                        logging.error("Concat function should have 1 argument - list of string expressions")
                        raise Exception("Concat function should have 1 argument - list of string expressions")
                elif key == JOIN:
                    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], list):
                        new_data = []
                        for elem in value[0]:
                            if isinstance(elem, (six.string_types, int, float)):
                                new_data += [str(elem)]
                            else:
                                new_data += [str(self.resolve_get_attribute_and_intrinsic_functions(elem, tmpl_name))]
                        new_data = str(value[1]).join(new_data)
                    else:
                        logging.error("Join function should have 1 argument - list with 2 elements: "
                                      "list of string expressions and delimiter")
                        raise Exception("Join function should have 1 argument - list with 2 elements: "
                                        "list of string expressions and delimiter")
                elif key == TOKEN:
                    if isinstance(value, list) and len(value) == 3:
                        if isinstance(value[0], (six.string_types, int, float)):
                            value[0] = str(value[0])
                        else:
                            value[0] = str(self.resolve_get_attribute_and_intrinsic_functions(value[0], tmpl_name))
                        if isinstance(value[1], (six.string_types, int, float)):
                            value[1] = str(value[1])
                        else:
                            value[1] = str(self.resolve_get_attribute_and_intrinsic_functions(value[1], tmpl_name))
                        if isinstance(value[2], (six.string_types, int, float)):
                            value[2] = int(value[2])
                        else:
                            value[2] = int(self.resolve_get_attribute_and_intrinsic_functions(value[2], tmpl_name))
                        new_data = value[0].split(value[1])[value[2]]
                    else:
                        logging.error("Token function should have 1 argument - list with 3 elements: "
                                      "string_with_tokens, string_of_token_chars, substring_index")
                        raise Exception("Token function should have 1 argument - list with 3 elements: "
                                        "string_with_tokens, string_of_token_chars, substring_index")
                else:
                    new_data[key] = self.resolve_get_attribute_and_intrinsic_functions(value,
                                                                        tmpl_name if tmpl_name is not None else key)
            return new_data
        elif isinstance(data, list):
            new_data = []
            for v in data:
                new_data.append(self.resolve_get_attribute_and_intrinsic_functions(v, tmpl_name))
            return new_data
        elif isinstance(data, GetAttribute):
            value = data.args
            return self._get_attribute_value(value, tmpl_name)
        elif isinstance(data, GetProperty):
            value = data.args
            return self._get_attribute_value(value, tmpl_name)
        elif isinstance(data, GetInput):
            value = data.args
            return self._get_input_value(value, tmpl_name)
        elif isinstance(data, Concat):
            value = data.args
            if isinstance(value, list):
                new_data = ""
                for elem in value:
                    if isinstance(elem, (six.string_types, int, float)):
                        new_data += str(elem)
                    else:
                        new_data += str(self.resolve_get_attribute_and_intrinsic_functions(elem, tmpl_name))
            else:
                logging.error("Concat function should have 1 argument - list of string expressions")
                raise Exception("Concat function should have 1 argument - list of string expressions")
            return new_data
        elif isinstance(data, Token):
            value = data.args
            if isinstance(value, list) and len(value) == 3:
                if isinstance(value[0], (six.string_types, int, float)):
                    value[0] = str(value[0])
                else:
                    value[0] = str(self.resolve_get_attribute_and_intrinsic_functions(value[0], tmpl_name))
                if isinstance(value[1], (six.string_types, int, float)):
                    value[1] = str(value[1])
                else:
                    value[1] = str(self.resolve_get_attribute_and_intrinsic_functions(value[1], tmpl_name))
                if isinstance(value[2], (six.string_types, int, float)):
                    value[2] = int(value[2])
                else:
                    value[2] = int(self.resolve_get_attribute_and_intrinsic_functions(value[2], tmpl_name))
                new_data = value[0].split(value[1])[value[2]]
            else:
                logging.error("Token function should have 1 argument - list with 3 elements: "
                              "string_with_tokens, string_of_token_chars, substring_index")
                raise Exception("Token function should have 1 argument - list with 3 elements: "
                                "string_with_tokens, string_of_token_chars, substring_index")
            return new_data
        return data

    def _get_input_value(self, value, tmpl_name):
        if isinstance(value, list):
            result = self.inputs.get(value[0])
            default = result.get(DEFAULT)
            if not result or not default:
                logging.error('No input with name %s' % value[0])
                raise Exception('No input with name %s' % value[0])
            return default
        else:
            logging.error('Parameter of get_input should be a list')
            raise Exception('Parameter of get_input should be a list')

    def _get_attribute_value(self, value, tmpl_name):
        if isinstance(value, list):
            attr_keys = []
            tmpl_attrs = None

            if value[0] == 'SELF':
                value[0] = tmpl_name
            if value[0] == 'HOST':
                value = [tmpl_name, 'host'] + value[1:]
            if value[0] == 'SOURCE':
                found = False
                for v in self.operations_graph:
                    if v.name == tmpl_name:
                        found = True
                        value[0] = v.source
                if not found:
                    logging.error("Relationship %s not found" % tmpl_name)
                    raise Exception("Relationship %s not found" % tmpl_name)
            if value[0] == 'TARGET':
                found = False
                for v in self.operations_graph:
                    if v.name == tmpl_name:
                        found = True
                        value[0] = v.target
                if not found:
                    logging.error("Relationship %s not found" % tmpl_name)
                    raise Exception("Relationship %s not found" % tmpl_name)

            template, type = get_actual_state_of_instance_model(self.cluster_name, value[0])
            if type == NODES:
                node_tmpl = template
                if node_tmpl.get(REQUIREMENTS, None) is not None:
                    for req in node_tmpl[REQUIREMENTS]:
                        if req.get(value[1], None) is not None:
                            if req[value[1]].get(NODE, None) is not None:
                                return self._get_attribute_value([req[value[1]][NODE]] + value[2:], req[value[1]][NODE])
                            if req[value[1]].get(NODE_FILTER, None) is not None:
                                tmpl_attrs = {}
                                node_filter_attrs = req[value[1]][NODE_FILTER].get(ATTRIBUTES, [])
                                for attr in node_filter_attrs:
                                    tmpl_attrs.update(attr)
                                attr_keys = value[2:]
                if node_tmpl.get(CAPABILITIES, {}).get(value[1], None) is not None:
                    tmpl_attrs = node_tmpl[CAPABILITIES][value[1]].get(ATTRIBUTES, {})
                    attr_keys = value[2:]
                if node_tmpl.get(ATTRIBUTES, {}).get(value[1], None) is not None:
                    tmpl_attrs = node_tmpl[ATTRIBUTES]
                    attr_keys = value[1:]
            elif type == RELATIONSHIPS:
                rel_tmpl = template
                if rel_tmpl.get(ATTRIBUTES, {}).get(value[1], None) is not None:
                    tmpl_attrs = rel_tmpl[ATTRIBUTES]
                    attr_keys = value[1:]
            else:
                logging.error("Value %s not found in %s" % (value[0], tmpl_name))
                raise Exception("Value %s not found in %s" % (value[0], tmpl_name))

            for key in attr_keys:
                if tmpl_attrs.get(key, None) is None:
                    tmpl_attrs = None
                    break
                tmpl_attrs = tmpl_attrs[key]
            if tmpl_attrs is None:
                logging.error("Failed to get attribute: %s" % json.dumps(value))
                raise Exception("Failed to get attribute: %s" % json.dumps(value))
            return tmpl_attrs
        else:
            logging.error('Parameter of get_attribute should be a list')
            raise Exception('Parameter of get_attribute should be a list')

    def _get_property_value(self, value, tmpl_name):
        if isinstance(value, list):
            prop_keys = []
            tmpl_properties = None

            if value[0] == 'SELF':
                value[0] = tmpl_name
            if value[0] == 'HOST':
                value = [tmpl_name, 'host'] + value[1:]
            if value[0] == 'SOURCE':
                found = False
                for v in self.operations_graph:
                    if v.name == tmpl_name:
                        found = True
                        value[0] = v.source
                if not found:
                    logging.error("Relationship %s not found" % tmpl_name)
                    raise Exception("Relationship %s not found" % tmpl_name)
            if value[0] == 'TARGET':
                found = False
                for v in self.operations_graph:
                    if v.name == tmpl_name:
                        found = True
                        value[0] = v.target
                if not found:
                    logging.error("Relationship %s not found" % tmpl_name)
                    raise Exception("Relationship %s not found" % tmpl_name)

            template, type = get_actual_state_of_instance_model(self.cluster_name, value[0])

            if type == NODES:
                node_tmpl = template
                if node_tmpl.get(REQUIREMENTS, None) is not None:
                    for req in node_tmpl[REQUIREMENTS]:
                        if req.get(value[1], None) is not None:
                            if req[value[1]].get(NODE, None) is not None:
                                return self._get_property_value([req[value[1]][NODE]] + value[2:], req[value[1]][NODE])
                            if req[value[1]].get(NODE_FILTER, None) is not None:
                                tmpl_properties = {}
                                node_filter_props = req[value[1]][NODE_FILTER].get(PROPERTIES, [])
                                for prop in node_filter_props:
                                    tmpl_properties.update(prop)
                                prop_keys = value[2:]
                if node_tmpl.get(CAPABILITIES, {}).get(value[1], None) is not None:
                    tmpl_properties = node_tmpl[CAPABILITIES][value[1]].get(PROPERTIES, {})
                    prop_keys = value[2:]
                if node_tmpl.get(PROPERTIES, {}).get(value[1], None) is not None:
                    tmpl_properties = node_tmpl[PROPERTIES]
                    prop_keys = value[1:]
            elif type == RELATIONSHIPS:
                rel_tmpl = template
                if rel_tmpl.get(PROPERTIES, {}).get(value[1], None) is not None:
                    tmpl_properties = rel_tmpl[PROPERTIES]
                    prop_keys = value[1:]
            else:
                logging.error("Value %s not found in %s" % (value[0], tmpl_name))
                raise Exception("Value %s not found in %s" % (value[0], tmpl_name))

            for key in prop_keys:
                if tmpl_properties.get(key, None) is None:
                    tmpl_properties = None
                    break
                tmpl_properties = tmpl_properties[key]
            if tmpl_properties is None:
                logging.error("Failed to get property: %s" % json.dumps(value))
                raise Exception("Failed to get property: %s" % json.dumps(value))
            return tmpl_properties
        else:
            logging.error('Parameter of get_property should be a list')
            raise Exception('Parameter of get_property should be a list')
