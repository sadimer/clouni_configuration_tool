import json
import os

import yaml
from toscaparser.functions import GetProperty, GetAttribute, GetInput, Concat, Token

import time
from queue import Queue

from graphlib import TopologicalSorter

from configuration_tool.common import utils
from configuration_tool.common.tosca_reserved_keys import NODES, RELATIONSHIPS, INTERFACES, \
    TYPE, ATTRIBUTES, PROPERTIES, CAPABILITIES, NODE_FILTER, NODE, REQUIREMENTS, DEFAULT, TOKEN, JOIN, \
    CONCAT, GET_PROPERTY, GET_INPUT, GET_ATTRIBUTE, OUTPUTS, ANSIBLE

import logging, six, copy

from configuration_tool.configuration_tools.common.instance_model.instance_model import \
    get_actual_state_of_instance_model, update_instance_model, delete_cluster_from_instance_model
from configuration_tool.configuration_tools.common.tool_config import ConfigurationToolConfiguration
from configuration_tool.providers.common.provider_configuration import ProviderConfiguration
from configuration_tool.runner.runner import grpc_cotea_run_ansible

REQUIRED_CONFIG_PARAMS = (INITIAL_ARTIFACTS_DIRECTORY, DEFAULT_HOST) = ("initial_artifacts_directory", "default_host")

OUTPUT_IDS = 'output_ids'
OUTPUT_ID_RANGE_START = 1000
OUTPUT_ID_RANGE_END = 9999


SEPARATOR = '.'

class ConfigurationTool(object):

    def __init__(self, provider=None):
        if not hasattr(self, 'TOOL_NAME'):
            raise NotImplementedError()

        self.provider = provider
        self.tool_config = ConfigurationToolConfiguration(self.TOOL_NAME)

        main_config = self.tool_config.get_section('main')

        for param in REQUIRED_CONFIG_PARAMS:
            if not param in main_config.keys():
                logging.error("Configuration parameter \'%s\' is missing in configuration" % param)
                raise Exception("Configuration parameter \'%s\' is missing in configuration" % param)

        for param in REQUIRED_CONFIG_PARAMS:
            setattr(self, param, main_config[param])

    def to_dsl(self, provider, operations_graph, reversed_operations_graph, cluster_name, is_delete,
               target_directory=None, extra=None, debug=False, grpc_cotea_endpoint=None):

        provider_config = ProviderConfiguration(self.provider)
        provider_tool_config = provider_config.get_section(self.TOOL_NAME)
        node_filter_config = provider_config.get_subsection(ANSIBLE, NODE_FILTER)

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
            node_values = None
            # try to get new finished operation from queue and find it in list of active
            # if get - mark done this operation (but before it find in graph)
            # if ready operations exists - get it and execute, remove from active
            try:
                node_values = q.get_nowait()
            except Exception:
                time.sleep(1)
            if isinstance(node_values, Exception):
                logging.error("Deploy failed with %s" % node_values)
                raise Exception("Deploy failed with %s" % node_values)
            if node_values is not None:
                if isinstance(node_values, dict) and len(node_values) == 1:
                    node_name = list(node_values.keys())[0]
                    for node in active:
                        if node.name == node_name.split(SEPARATOR)[0] and node.operation == node_name.split(SEPARATOR)[1]:
                            active.remove(node)
                            elements.done(node)
                            update_instance_model(self.cluster_name, node.tmpl, node.type, node.name,
                                                  node_values[node_name].get(ATTRIBUTES, []),
                                                  node_values[node_name].get(PROPERTIES, []), is_delete)
                            self.resolve_outputs(node_values[node_name][OUTPUTS], node, is_delete)
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
                description_prefix, module_prefix = self.get_module_prefixes(is_delete, provider_tool_config)
                description_by_type = self.description_by_type(v.type_name, description_prefix)
                module_by_type = self.module_by_type(v.type_name, module_prefix, provider_tool_config.get('module_exceptions'))
                ansible_tasks = []
                if not is_delete:
                    result = self.resolve_get_attribute_and_intrinsic_functions({v.name: v.tmpl})
                    v.tmpl = result[v.name]
                host = self.default_host
                # create playbook for every operation
                if v.operation == 'delete':
                    if not v.is_software_component:
                        tasks = self.get_for_delete(v, cluster_name, description_by_type, module_by_type,
                                                                  additional_args=extra)
                        tasks.extend(
                            self.get_from_interface(v, target_directory, is_delete, v.operation,
                                                                  cluster_name,
                                                                  additional_args=extra))
                        if not any(item == module_by_type for item in
                                   provider_tool_config.get('modules_skipping_delete', [])):
                            ansible_tasks.extend(copy.deepcopy(tasks))
                    else:
                        ansible_tasks.extend(copy.deepcopy(
                            self.get_from_interface(v, target_directory, is_delete, v.operation,
                                                                  cluster_name,
                                                                  additional_args=extra)))
                        host = v.host
                elif v.operation == 'create':
                    if not v.is_software_component:
                        ansible_tasks.extend(self.get_for_create(v, target_directory, node_filter_config,
                                                                               description_by_type, module_by_type,
                                                                               additional_args=extra))
                        ansible_tasks.extend(
                            self.get_from_interface(v, target_directory, is_delete, v.operation,
                                                                  cluster_name,
                                                                  additional_args=extra))

                    else:
                        ansible_tasks.extend(copy.deepcopy(
                            self.get_from_interface(v, target_directory, is_delete, v.operation,
                                                                  cluster_name,
                                                                  additional_args=extra)))
                        host = v.host
                else:
                    (_, element_type, _) = utils.tosca_type_parse(v.type)
                    if element_type == NODES:
                        if v.is_software_component:
                            host = v.host
                    # operations for relationships executes on target/source host depends on operation
                    elif element_type == RELATIONSHIPS:
                        if v.operation == 'pre_configure_target' or v.operation == 'post_configure_target' or v.operation == 'add_source':
                            for elem in operations_graph:
                                if elem.name == v.target:
                                    if elem.is_software_component:
                                        host = v.host
                                    break
                        elif v.operation == 'pre_configure_source' or v.operation == 'post_configure_source':
                            for elem in operations_graph:
                                if elem.name == v.source:
                                    if elem.is_software_component:
                                        host = elem.host
                                    break
                        else:
                            logging.error("Unsupported operation for relationship in operation graph")
                            raise Exception("Unsupported operation for relationship in operation graph")
                    else:
                        logging.error("Unsupported element type in operation graph")
                        raise Exception("Unsupported element type in operation graph")
                    ansible_tasks.extend(copy.deepcopy(
                        self.get_from_interface(v, target_directory, is_delete, v.operation, cluster_name,
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
                        outputs, _ = self.get_outputs(v)
                        if v.tmpl.get(PROPERTIES):
                            properties = list(v.tmpl.get(PROPERTIES).keys())
                        else:
                            properties = []
                        self.run(ansible_tasks, grpc_cotea_endpoint, host, v.name, v.operation, q, extra,
                                 provider_tool_config, self.get_defined_attributes(v),
                                 outputs, properties)
                    else:
                        elements.done(v)
                    active.append(v)
        if not debug and is_delete:
            delete_cluster_from_instance_model(cluster_name)
        return yaml.dump(ansible_playbook, default_flow_style=False)

    def get_from_interface(self, element_object, target_directory, is_delete, operation, cluster_name,
                           additional_args=None):
        raise NotImplementedError

    def get_for_delete(self, element_object, cluster_name, description_by_type, module_by_type,
                           additional_args=None):
        raise NotImplementedError

    def get_for_create(self, element_object, target_directory, node_filter_config, description_by_type,
                           module_by_type, additional_args=None):
        raise NotImplementedError

    @staticmethod
    def get_interfaces_from_node(node):
        return node.tmpl.get(INTERFACES, {})

    @staticmethod
    def get_interfaces_from_relationship(rel):
        return rel.tmpl.get(INTERFACES, {})

    @staticmethod
    def get_defined_attributes(provider_tpl):
        return list(provider_tpl.type_definition.get(ATTRIBUTES))

    def get_artifact_extension(self):
        return '.yaml'

    def resolve_outputs(self, outputs_val, resource, is_delete):
        outputs, attrs = self.get_outputs(resource)
        for output, attr in zip(outputs, attrs):
            attr = self._resolve_tosca_travers(attr, resource.name)
            found = False
            for v in self.operations_graph:
                if v.name == attr[0]:
                    found = True
                    for output_val in outputs_val:
                        if output_val.get(output):
                            update_instance_model(self.cluster_name, v.tmpl, v.type, v.name,
                                                  [{attr[1]: output_val[output]}], [], is_delete)
            if not found:
                logging.error('Template with name %s not found' % attr[0])
                raise Exception('Template with name %s not found' % attr[0])

    def get_outputs(self, resource):
        res_outputs = []
        res_attrs = []
        operation = resource.operation
        interfaces = resource.tmpl.get(INTERFACES)
        if interfaces:
            for interface, value in interfaces.items():
                op = value.get(operation)
                if op:
                    outputs = op.get(OUTPUTS)
                    if outputs:
                        for output, attr in outputs.items():
                            res_outputs.append(output)
                            res_attrs.append(attr)
        return res_outputs, res_attrs

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

    def _resolve_tosca_travers(self, value, tmpl_name):
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

        template = get_actual_state_of_instance_model(self.cluster_name, value[0], 1)
        (_, type, _) = utils.tosca_type_parse(template.get(TYPE))
        if type == NODES:
            node_tmpl = template
            if node_tmpl.get(REQUIREMENTS, None) is not None:
                for req in node_tmpl[REQUIREMENTS]:
                    if req.get(value[1], None) is not None:
                        if req[value[1]].get(NODE, None) is not None:
                            return self._resolve_tosca_travers([req[value[1]][NODE]] + value[2:], req[value[1]][NODE])
        return value

    def _get_attribute_value(self, value, tmpl_name):
        if isinstance(value, list):
            attr_keys = []
            tmpl_attrs = None
            value = self._resolve_tosca_travers(value, tmpl_name)
            template = get_actual_state_of_instance_model(self.cluster_name, value[0], 1)
            (_, type, _) = utils.tosca_type_parse(template.get(TYPE))
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
            value = self._resolve_tosca_travers(value, tmpl_name)
            template = get_actual_state_of_instance_model(self.cluster_name, value[0], 1)
            (_, type, _) = utils.tosca_type_parse(template.get(TYPE))
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

    def run(self, ansible_tasks, grpc_cotea_endpoint, hosts, name, op, q, extra,
            provider_tool_config, attributes, outputs, properties):
        extra_env = {}
        extra_vars = extra.get('global')
        plugins_path = os.path.join(utils.get_tmp_clouni_dir(), 'ansible_plugins/plugins/modules/cloud/', self.provider)
        grpc_cotea_run_ansible(ansible_tasks, grpc_cotea_endpoint, extra_env, extra_vars, hosts, name, op, q,
                               provider_tool_config,
                               ansible_library=plugins_path,
                               attributes=attributes,
                               outputs=outputs,
                               properties=properties)

    @staticmethod
    def description_by_type(provider_source_obj_type, description_prefix):
        return description_prefix + ' ' + utils.snake_case(provider_source_obj_type).replace('_', ' ')

    @staticmethod
    def module_by_type(provider_source_obj_type, module_prefix, exceptions):
        provider_source_obj_type = utils.snake_case(provider_source_obj_type)
        if exceptions is None or provider_source_obj_type not in exceptions:
            return module_prefix + provider_source_obj_type
        else:
            return exceptions[provider_source_obj_type]

    def get_module_prefixes(self, is_delete, provider_tool_config=None, low=False):
        if is_delete:
            desc = 'Delete'
        else:
            desc = 'Create'
        module_prefix = ''
        if provider_tool_config:
            new_module_desc = provider_tool_config.get('module_description' + '_' + desc.lower())
            if new_module_desc:
                desc = new_module_desc
            new_module_prefix = provider_tool_config.get('module_prefix')
            if new_module_prefix:
                module_prefix = new_module_prefix
        if low:
            desc = desc.lower()
        return desc, module_prefix