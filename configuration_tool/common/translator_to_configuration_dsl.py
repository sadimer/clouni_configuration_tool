import logging
import os
import sys

import yaml
from yaml import Loader

from configuration_tool.common.tosca_reserved_keys import IMPORTS, DEFAULT_ARTIFACTS_DIRECTORY, \
    EXECUTOR, NAME, TOSCA_ELEMENTS_MAP_FILE, TOSCA_ELEMENTS_DEFINITION_FILE, TOPOLOGY_TEMPLATE
from configuration_tool.common import utils
from configuration_tool.common.configuration import Configuration
from configuration_tool.configuration_tools.combined.combine_configuration_tools import get_configuration_tool_class
from configuration_tool.providers.common.tosca_template import ProviderToscaTemplate

REQUIRED_CONFIGURATION_PARAMS = (TOSCA_ELEMENTS_DEFINITION_FILE, DEFAULT_ARTIFACTS_DIRECTORY, TOSCA_ELEMENTS_MAP_FILE)



REQUIRED_CONFIGURATION_PARAMS = (TOSCA_ELEMENTS_DEFINITION_FILE, DEFAULT_ARTIFACTS_DIRECTORY, TOSCA_ELEMENTS_MAP_FILE)


def translate(provider_template, configuration_tool, cluster_name, is_delete=False,
              extra=None, log_level='info', debug=False, host_ip_parameter='public_address'):
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
            raise Exception('Provider configuration parameter "%s" is missing in configuration file' % sec)

    provider_template = yaml.load(provider_template, Loader=Loader)
    provider_template = provider_template.get(TOPOLOGY_TEMPLATE)

    # tmp version - provider gets from first node template (can't use different providers in template)
    provider = None
    for key in provider_template.get('node_templates').keys():
        provider_template_name = key
        tosca_type = provider_template.get('node_templates').get(provider_template_name).get('type')
        (provider, _, _) = utils.tosca_type_parse(tosca_type)
        if provider is not None and provider != 'tosca':
            break

    tosca = ProviderToscaTemplate(provider_template, provider, configuration_tool, cluster_name,
                                  host_ip_parameter, is_delete)

    tool = get_configuration_tool_class(configuration_tool)(provider)

    default_artifacts_directory = config.get_section(config.MAIN_SECTION).get(DEFAULT_ARTIFACTS_DIRECTORY)

    configuration_content = tool.to_dsl(provider, tosca.provider_operations, tosca.reversed_provider_operations,
                                        tosca.cluster_name, is_delete, target_directory=default_artifacts_directory,
                                        inputs=tosca.inputs, outputs=tosca.outputs, extra=extra, debug=debug)
    return configuration_content


