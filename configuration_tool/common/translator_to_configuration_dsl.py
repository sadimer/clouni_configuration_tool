import ast
import json
import logging
import os
import sys

import six

from configuration_tool.common.tosca_reserved_keys import IMPORTS, DEFAULT_ARTIFACTS_DIRECTORY,\
    EXECUTOR, NAME, TOSCA_ELEMENTS_MAP_FILE, TOSCA_ELEMENTS_DEFINITION_FILE
from configuration_tool.common import utils
from configuration_tool.common.configuration import Configuration
from configuration_tool.configuration_tools.combined.combine_configuration_tools import get_configuration_tool_class
from configuration_tool.providers.common.tosca_template import ProviderToscaTemplate

REQUIRED_CONFIGURATION_PARAMS = (TOSCA_ELEMENTS_DEFINITION_FILE, DEFAULT_ARTIFACTS_DIRECTORY, TOSCA_ELEMENTS_MAP_FILE)



REQUIRED_CONFIGURATION_PARAMS = (TOSCA_ELEMENTS_DEFINITION_FILE, DEFAULT_ARTIFACTS_DIRECTORY, TOSCA_ELEMENTS_MAP_FILE)


def translate(provider_template, configuration_tool, cluster_name, is_software_component,
              is_delete=False, extra=None, log_level='info', host_ip_parameter='public_address', debug=False):
    log_map = dict(
        debug=logging.DEBUG,
        info=logging.INFO,
        warning=logging.WARNING,
        error=logging.ERROR,
        critical=logging.ERROR
    )

    logging_format = "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(filename=os.path.join(os.getenv('HOME'), '.clouni.log'), filemode='a', level=log_map[log_level],
                        format=logging_format, datefmt='%Y-%m-%d %H:%M:%S')

    config = Configuration()
    for sec in REQUIRED_CONFIGURATION_PARAMS:
        if sec not in config.get_section(config.MAIN_SECTION).keys():
            logging.error('Provider configuration parameter "%s" is missing in configuration file' % sec)
            sys.exit(1)

    map_files = config.get_section(config.MAIN_SECTION).get(TOSCA_ELEMENTS_MAP_FILE)
    if isinstance(map_files, six.string_types):
        map_files = [map_files]
    default_map_files = []
    for map_file in map_files:
        default_map_files.append(os.path.join(utils.get_project_root_path(), map_file))
    logging.info("Default TOSCA template map file to be used \'%s\'" % json.dumps(default_map_files))

    # Parse and generate new TOSCA service template with only provider specific TOSCA types from normative types
    tosca = ProviderToscaTemplate(provider_template, provider, configuration_tool, cluster_name,
                                  host_ip_parameter, is_delete, common_map_files=default_map_files)

    provider_template_name = list(provider_template.keys())[0]
    tosca_type = provider_template_name.get(provider_template_name).get('type')
    (provider, _, _) = utils.tosca_type_parse(tosca_type)
    tool = get_configuration_tool_class(configuration_tool)(provider)

    default_artifacts_directory = config.get_section(config.MAIN_SECTION).get(DEFAULT_ARTIFACTS_DIRECTORY)

    configuration_content = tool.to_dsl(tosca.provider_operations, tosca.reversed_provider_operations,
                                        tosca.cluster_name, is_delete,
                                        artifacts=tool_artifacts, target_directory=default_artifacts_directory,
                                        inputs=tosca.inputs, outputs=tosca.outputs, extra=extra_full, debug=debug)
    return configuration_content


