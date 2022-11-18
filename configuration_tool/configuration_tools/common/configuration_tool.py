from configuration_tool.common import utils
from configuration_tool.common.tosca_reserved_keys import NODES, RELATIONSHIPS, INTERFACES, GET_OPERATION_OUTPUT, SELF, \
    IMPLEMENTATION, TYPE

import logging, sys, six, copy

from configuration_tool.configuration_tools.common.tool_config import ConfigurationToolConfiguration

OUTPUT_IDS = 'output_ids'
OUTPUT_ID_RANGE_START = 1000
OUTPUT_ID_RANGE_END = 9999


class ConfigurationTool(object):

    def __init__(self):
        if not hasattr(self, 'TOOL_NAME'):
            raise NotImplementedError()

        self.global_operations_queue = []
        self.global_operations_info = {}
        self.global_variables = {}

        self.tool_config = ConfigurationToolConfiguration(self.TOOL_NAME)

    def to_dsl(self, provider, nodes_relationships_queue, reversed_nodes_relationships_queue,
               cluster_name, is_delete, target_directory=None, extra=None):
        """
        Generate scenarios for configuration tool to execute
        :param provider: provider type key name
        :param reversed_nodes_relationships_queue: can be of class ProviderResource or RelationshipTemplate
        :param nodes_relationships_queue: can be of class ProviderResource or RelationshipTemplate
        :param cluster_name: unified name of cluster of template
        :param is_delete: boolean value that means if scenario should create or delete cluster
        :param artifacts: list of artifacts that are mentioned in template
        :param target_directory: directory where copy artifacts
        :param extra: extra parameters for configuration tool scenarios
        :return: string with dsl scenario which is used to deploy
        """
        raise NotImplementedError()

    def create_artifact(self, filename, data):
        """

        :param filename:
        :param data:
        :return:
        """
        raise NotImplementedError()

    def get_interfaces_from_node(self, node):
        """

        :param node:
        :return:
        """
        return node.tmpl.get(INTERFACES, {})

    def get_interfaces_from_relationship(self, rel):
        """

        :param rel:
        :return:
        """
        return rel.tmpl.get(INTERFACES, {})

    def manage_operation_output(self, op_required, element_template_name):
        """

        :param op_required:
        :param element_template_name:
        :return:
        """
        for o in op_required:
            if o[0] == SELF:
                o[0] = element_template_name
            temp_op_name = '_'.join(o[:3]).lower()

            output_id = o[-1] + '_' + str(utils.get_random_int(OUTPUT_ID_RANGE_START, OUTPUT_ID_RANGE_END))
            updating_op_info = {
                temp_op_name: {
                    OUTPUT_IDS: {
                        o[-1]: output_id
                    }
                }
            }
            utils.deep_update_dict(self.global_operations_info, updating_op_info)

    def list_get_operation_outputs(self, data):
        """

        :param data:
        :return:
        """
        required_operations = []
        if isinstance(data, dict):
            for k, v in data.items():
                if k == GET_OPERATION_OUTPUT:
                    required_operations.append(v)
                else:
                    required_operations.extend(self.list_get_operation_outputs(v))
        elif isinstance(data, list):
            for v in data:
                required_operations.extend(self.list_get_operation_outputs(v))

        return required_operations

    def get_artifact_extension(self):
        raise NotImplementedError()
