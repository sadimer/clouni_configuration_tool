from toscaparser.imports import ImportsLoader

from configuration_tool.common import utils
from configuration_tool.common.tosca_reserved_keys import *

from configuration_tool.providers.common.provider_configuration import ProviderConfiguration
from configuration_tool.providers.common.provider_resource import ProviderResource


import os, copy, logging, sys

SEPARATOR = ':'

class ProviderToscaTemplate(object):
    REQUIRED_CONFIG_PARAMS = (TOSCA_ELEMENTS_MAP_FILE, TOSCA_ELEMENTS_DEFINITION_FILE)
    DEPENDENCY_FUNCTIONS = (GET_PROPERTY, GET_ATTRIBUTE, GET_OPERATION_OUTPUT)
    DEFAULT_ARTIFACTS_DIRECTOR = ARTIFACTS

    def __init__(self, topology_template, provider, configuration_tool, cluster_name, host_ip_parameter, is_delete, common_map_files=[]):
        self.host_ip_parameter = host_ip_parameter
        self.provider = provider
        self.is_delete = is_delete
        self.configuration_tool = configuration_tool
        self.provider_config = ProviderConfiguration(self.provider)
        self.cluster_name = cluster_name
        self.software_types = set()
        self.fulfil_definitions_with_parents()

        if topology_template[NODE_TEMPLATES]:
            self.node_templates = topology_template[NODE_TEMPLATES]
        if topology_template[RELATIONSHIP_TEMPLATES]:
            self.relationship_templates = topology_template[RELATIONSHIP_TEMPLATES]
        if topology_template[OUTPUTS]:
            self.outputs = topology_template[OUTPUTS]
        if topology_template[INPUTS]:
            self.inputs = topology_template[INPUTS]

        for sec in self.REQUIRED_CONFIG_PARAMS:
            if not self.provider_config.config[self.provider_config.MAIN_SECTION].get(sec):
                logging.error("Provider configuration parameter \'%s\' has missing value" % sec)
                logging.error("Translating failed")
                sys.exit(1)

        self.definitions = {}
        import_definition_file = ImportsLoader([self.definition_file()], None, list(SERVICE_TEMPLATE_KEYS),
                                               topology_template.tpl)
        self.definitions.update(import_definition_file.get_custom_defs())

        self.configuration_content = None
        self.configuration_ready = None

        self.template_dependencies = dict()
        self._relation_target_source = dict()
        self.resolve_in_template_dependencies()

        # After this step self.node_templates has requirements with node_filter parameter
        self.replace_requirements_with_node_filter()
        self.provider_nodes = self._provider_nodes()
        self.provider_relations = self._provider_relations()

        self.provider_operations, self.reversed_provider_operations = self.sort_nodes_and_operations_by_graph_dependency()

    def resolve_in_template_dependencies(self):
        """
        TODO think through the logic to replace mentions by id
        Changes all mentions of node_templates by name in requirements, places dictionary with node_filter instead
        :return:
        """
        for node_name, node in self.node_templates.items():
            for req in node.get(REQUIREMENTS, []):
                for req_name, req_body in req.items():

                    # Valid keys are ('node', 'node_filter', 'relationship', 'capability', 'occurrences')
                    # Only node and relationship might be a template name or a type
                    req_relationship = req_body.get(RELATIONSHIP)
                    req_node = req_body.get(NODE)

                    if req_relationship is not None:
                        (_, _, type_name) = utils.tosca_type_parse(req_relationship)
                        if type_name is None:
                            self.add_template_dependency(node_name, req_relationship)
                            self._relation_target_source[req_relationship] = {
                                'source': node_name,
                                'target': req_node
                            }

                    if req_node is not None:
                        (_, _, type_name) = utils.tosca_type_parse(req_node)
                        if type_name is None:
                            self.add_template_dependency(node_name, req_node)

            node_types_from_requirements = set()
            req_definitions = self.definitions[node[TYPE]].get(REQUIREMENTS, [])
            for req in req_definitions:
                for req_name, req_def in req.items():
                    if req_def.get(NODE, None) is not None:
                        if req_def[NODE] != node[TYPE]:
                            node_types_from_requirements.add(req_def[NODE])
            for req_node_name, req_node_tmpl in self.node_templates.items():
                if req_node_tmpl[TYPE] in node_types_from_requirements:
                    self.add_template_dependency(node_name, req_node_name)

    def add_template_dependency(self, node_name, dependency_name):
        if not dependency_name == SELF and not node_name == dependency_name:
            if self.template_dependencies.get(node_name) is None:
                self.template_dependencies[node_name] = {dependency_name}
            else:
                self.template_dependencies[node_name].add(dependency_name)

    def definition_file(self):
        file_definition = self.provider_config.config['main'][TOSCA_ELEMENTS_DEFINITION_FILE]
        if not os.path.isabs(file_definition):
            file_definition = os.path.join(self.provider_config.config_directory, file_definition)

        if not os.path.isfile(file_definition):
            logging.error("TOSCA definition file not found: %s" % file_definition)
            sys.exit(1)

        return file_definition

    def replace_requirements_with_node_filter(self):
        for node_name, node in self.node_templates.items():
            for req in node.get(REQUIREMENTS, []):
                for req_name, req_body in req.items():
                    if req_body.get(NODE):
                        node_tmpl = self.node_templates.get(req_body[NODE])
                        node_filter = dict()
                        properties = node_tmpl.get(PROPERTIES)
                        props_list = []
                        if properties:
                            for prop_name, prop in properties.items():
                                props_list.append({prop_name: prop})
                        capabilities = node_tmpl.get(CAPABILITIES)
                        caps_list = []
                        if capabilities:
                            for cap_name, cap in capabilities.items():
                                cap_props = cap.get(PROPERTIES, {})
                                cap_props_list = []
                                for prop_name, prop in cap_props.items():
                                    cap_props_list.append({prop_name, prop})
                                caps_list.append({PROPERTIES: cap_props_list})

                        if properties:
                            node_filter[PROPERTIES] = props_list
                        if capabilities:
                            node_filter[CAPABILITIES] = caps_list
                        req_body[NODE_FILTER] = node_filter
                        req[req_name] = req_body

    def _provider_nodes(self):
        """
        Create a list of ProviderResource classes to represent a node in TOSCA
        :return: list of class objects inherited from ProviderResource
        """
        provider_nodes = dict()
        for node_name, node in self.node_templates.items():
            (namespace, category, type_name) = utils.tosca_type_parse(node[TYPE])
            is_software_component = node[TYPE] in self.software_types
            if namespace != self.provider and not is_software_component or category != NODES:
                logging.error('Unexpected values: node \'%s\' not a software component and has a provider \'%s\'. '
                              'Node will be ignored' % (node.name, namespace))
            else:
                provider_node_instance = ProviderResource(self.provider, self.is_delete, self.cluster_name, self.configuration_tool, node,
                                                          node_name,
                                                          self.host_ip_parameter, self.definitions[node[TYPE]],
                                                          is_software_component=is_software_component)
                provider_nodes[node_name] = provider_node_instance
        return provider_nodes

    def _provider_relations(self):
        provider_relations = dict()
        for rel_name, rel_body in self.relationship_templates.items():
            provider_rel_instance = ProviderResource(self.provider, self.is_delete, self.cluster_name, self.configuration_tool, rel_body,
                                                     rel_name,
                                                     self.host_ip_parameter, self.definitions[rel_body[TYPE]],
                                                     is_relationship=True,
                                                     relation_target_source=self._relation_target_source)
            provider_relations[rel_name] = provider_rel_instance
        return provider_relations

    def _provider_nodes_by_name(self):
        """
        Get provider_nodes_by_name
        :return: self.provider_nodes_by_name
        """

        provider_nodes_by_name = dict()
        for node in self.provider_nodes:
            provider_nodes_by_name[node.nodetemplate.name] = node

        return provider_nodes_by_name

    def sort_nodes_and_operations_by_graph_dependency(self):
        """
            This method generates dict fith ProviderTemplates with operation, sorted by
            dependencies from normative and provider TOSCA templates
        """
        nodes = set(self.provider_nodes.keys())
        nodes = nodes.union(set(self.provider_relations.keys()))
        dependencies = {}
        lifecycle = ['configure', 'start', 'stop', 'delete']
        reversed_full_lifecycle = lifecycle[::-1] + ['create']
        # generate only dependencies from nodes
        for templ_name in nodes:
            set_intersection = nodes.intersection(self.template_dependencies.get(templ_name, set()))
            templ = self.provider_nodes.get(templ_name, self.provider_relations.get(templ_name))
            (_, element_type, _) = utils.tosca_type_parse(templ.type)
            if element_type == NODES:
                if 'interfaces' in templ.tmpl and 'Standard' in templ.tmpl['interfaces']:
                    new_operations = ['create']
                    # operation create always exists
                    for elem in lifecycle:
                        if elem in templ.tmpl['interfaces']['Standard']:
                            new_operations.append(elem)
                    # if there is any other operations - add ti new_operations and translate to dict
                    # in format {node.op: {node1, node2}}
                    # node requieres node1 and node2
                    if len(new_operations) == 1:
                        utils.deep_update_dict(dependencies, {templ_name + SEPARATOR + 'create': set_intersection})
                    else:
                        for i in range(1, len(new_operations)):
                            utils.deep_update_dict(dependencies, {
                                templ_name + SEPARATOR + new_operations[i]: {
                                    templ_name + SEPARATOR + new_operations[i - 1]}})
                        utils.deep_update_dict(dependencies,
                                               {templ_name + SEPARATOR + new_operations[0]: set_intersection})
                else:
                    utils.deep_update_dict(dependencies, {templ_name + SEPARATOR + 'create': set_intersection})
        new_dependencies = {}
        # new_dependencies is needed for updating set operations
        # dict must be in format {node.op: {node1, node2}}
        for key, value in dependencies.items():
            new_set = set()
            for elem in value:
                for oper in reversed_full_lifecycle:
                    if elem + SEPARATOR + oper in dependencies:
                        new_set.add(elem + SEPARATOR + oper)
                        break
                    elif elem in dependencies:
                        new_set.add(elem)
                        break
            new_dependencies[key] = new_set

        # adding relationships operations pre_configure_source after create source node
        # pre_configure_target after create target node
        # add_source in parallel with pre_configure_source but in will be executed on target
        # post_configure_target after configure target node (if not configure then create - in parallel
        # with pre_configure_target)
        # post_configure_source after configure target node (if not configure then create - in parallel
        # with pre_configure_source)
        # other - not supported!
        for templ_name in nodes:
            templ = self.provider_nodes.get(templ_name, self.provider_relations.get(templ_name))
            (_, element_type, _) = utils.tosca_type_parse(templ.type)
            if element_type == RELATIONSHIPS:
                if 'interfaces' in templ.tmpl and 'Configure' in templ.tmpl['interfaces']:
                    if 'pre_configure_source' in templ.tmpl['interfaces']['Configure']:
                        new_dependencies = self.update_relationships(new_dependencies, templ.name, templ.source,
                                                                     'pre_configure_source', 'create', ['add_source'])
                    if 'pre_configure_target' in templ.tmpl['interfaces']['Configure']:
                        new_dependencies = self.update_relationships(new_dependencies, templ.name, templ.target,
                                                                     'pre_configure_target', 'create')
                    if 'post_configure_source' in templ.tmpl['interfaces']['Configure']:
                        if templ.source + SEPARATOR + 'configure' in new_dependencies:
                            new_dependencies = self.update_relationships(new_dependencies, templ.name, templ.source,
                                                                         'post_configure_source', 'configure')
                        else:
                            new_dependencies = self.update_relationships(new_dependencies, templ.name, templ.source,
                                                                         'post_configure_source', 'create')
                    if 'post_configure_target' in templ.tmpl['interfaces']['Configure']:
                        if templ.target + SEPARATOR + 'configure' in new_dependencies:
                            new_dependencies = self.update_relationships(new_dependencies, templ.name, templ.target,
                                                                         'post_configure_target', 'configure')
                        else:
                            new_dependencies = self.update_relationships(new_dependencies, templ.name, templ.target,
                                                                         'post_configure_target', 'create')
                    if 'add_source' in templ.tmpl['interfaces']['Configure']:
                        new_dependencies = self.update_relationships(new_dependencies, templ.name, templ.source,
                                                                     'add_source', 'create', ['pre_configure_source'])
                    if 'add_target' in templ.tmpl['interfaces']['Configure']:
                        logging.warning('Operation add_target not supported, it will be skipped')
                    if 'target_changed' in templ.tmpl['interfaces']['Configure']:
                        logging.warning('Operation target_changed not supported, it will be skipped')
                    if 'remove_target' in templ.tmpl['interfaces']['Configure']:
                        logging.warning('Operation remove_target not supported, it will be skipped')
        # mapping strings 'node.op' to provider template of this node with this operation
        templ_mappling = {}
        for elem in new_dependencies:
            templ_name = elem.split(SEPARATOR)[0]
            templ = {}
            templ['template'] = {templ_name: self.provider_nodes.get(templ_name, self.provider_relations.get(templ_name)).tmpl}
            templ['configuration_args'] = self.provider_nodes.get(templ_name, self.provider_relations.get(templ_name)).configuration_args
            templ['operation'] = elem.split(SEPARATOR)[1]
            templ_mappling[elem] = str(templ)
        templ_dependencies = {}
        reversed_templ_dependencies = {}
        # create dict where all elements will be replaced with provider template from templ_mappling
        # reversed_templ_dependencies needed for delete - it just a reversed version of graph
        for key, value in new_dependencies.items():
            new_list = []
            for elem in value:
                new_list.append(templ_mappling[elem])
                if templ_mappling[elem] not in reversed_templ_dependencies:
                    reversed_templ_dependencies[templ_mappling[elem]] = [templ_mappling[key]]
                elif templ_mappling[key] not in reversed_templ_dependencies[templ_mappling[elem]]:
                    reversed_templ_dependencies[templ_mappling[elem]].append(templ_mappling[key])
            templ_dependencies[templ_mappling[key]] = new_list
        if len(templ_dependencies) <= 1:
            reversed_templ_dependencies = copy.copy(templ_dependencies)
        return templ_dependencies, reversed_templ_dependencies

    def update_relationships(self, new_dependencies, templ_name, direction, rel_name, post_op, banned_ops=[]):
        utils.deep_update_dict(new_dependencies, {
            templ_name + SEPARATOR + rel_name: {direction + SEPARATOR + post_op}})
        for key, value in new_dependencies.items():
            for elem in value:
                if elem == direction + SEPARATOR + post_op and key != templ_name + SEPARATOR + rel_name and \
                        key not in [templ_name + SEPARATOR + x for x in banned_ops]:
                    utils.deep_update_dict(new_dependencies,
                                           {key: {templ_name + SEPARATOR + rel_name}})
        return new_dependencies

    def _get_full_defintion(self, definition, def_type, ready_set):
        if def_type in ready_set:
            return definition, def_type in self.software_types

        (_, _, def_type_short) = utils.tosca_type_parse(def_type)
        is_software_type = def_type_short == 'SoftwareComponent'
        is_software_parent = False
        parent_def_name = definition.get(DERIVED_FROM, None)
        if parent_def_name is not None:
            if def_type == parent_def_name:
                logging.critical("Invalid type \'%s\' is derived from itself" % def_type)
                sys.exit(1)
            if parent_def_name in ready_set:
                parent_definition = self.definitions[parent_def_name]
                is_software_parent = parent_def_name in self.software_types
            else:
                parent_definition, is_software_parent = \
                    self._get_full_defintion(self.definitions[parent_def_name], parent_def_name, ready_set)
            parent_definition = copy.deepcopy(parent_definition)
            definition = utils.deep_update_dict(parent_definition, definition)
        if is_software_type or is_software_parent:
            self.software_types.add(def_type)
        ready_set.add(def_type)
        return definition, def_type in self.software_types

    def fulfil_definitions_with_parents(self):
        ready_definitions = set()
        for def_name, definition in self.definitions.items():
            self.definitions[def_name], _ = self._get_full_defintion(definition, def_name, ready_definitions)
