import json
import logging
import os
from threading import Thread

import requests
import six
import yaml
from toscaparser.tosca_template import ToscaTemplate
from yaml import Loader

from configuration_tool.common.tosca_reserved_keys import IMPORTS, DEFAULT_ARTIFACTS_DIRECTORY, \
    EXECUTOR, NAME, TOSCA_ELEMENTS_MAP_FILE, TOSCA_ELEMENTS_DEFINITION_FILE, TOPOLOGY_TEMPLATE, TYPE, \
    TOSCA_ELEMENTS_DEFINITION_DB_CLUSTER_NAME
from configuration_tool.common import utils
from configuration_tool.common.configuration import Configuration
from configuration_tool.configuration_tools.combined.combine_configuration_tools import get_configuration_tool_class
from configuration_tool.providers.common.provider_configuration import ProviderConfiguration
from configuration_tool.providers.common.tosca_template import ProviderToscaTemplate

REQUIRED_CONFIGURATION_PARAMS = (TOSCA_ELEMENTS_DEFINITION_FILE, DEFAULT_ARTIFACTS_DIRECTORY, TOSCA_ELEMENTS_MAP_FILE)

REQUIRED_CONFIGURATION_PARAMS = (TOSCA_ELEMENTS_DEFINITION_FILE, DEFAULT_ARTIFACTS_DIRECTORY, TOSCA_ELEMENTS_MAP_FILE)


class NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True

def load_to_db(tosca, config, database_api_endpoint, template, cluster_name):
    definitions = {}
    all_templates = tosca.node_templates
    all_templates = utils.deep_update_dict(all_templates, tosca.relationship_templates)
    def_cluster = config.get_section(config.MAIN_SECTION).get(TOSCA_ELEMENTS_DEFINITION_DB_CLUSTER_NAME)
    for key, value in all_templates.items():
        type = value[TYPE]
        r = requests.get(utils.get_url_for_getting_dependencies(def_cluster, database_api_endpoint, type))
        try:
            response = r.json()
        except Exception:
            raise Exception("Failed to parse json response from db")
        if response['status'] != 200:
            raise Exception("Error in db! Status code: %s, msg: %s" % (response['status'], response['message']))
        definitions = utils.deep_update_dict(definitions, response['result'])
    with open(os.path.join(utils.get_tmp_clouni_dir(), 'template.yaml'), "w") as f:
        template = utils.deep_update_dict(template, definitions)
        del template[IMPORTS]
        print(yaml.dump(template, Dumper=NoAliasDumper), file=f)
    with open(os.path.join(utils.get_tmp_clouni_dir(), 'template.yaml'), "r") as f:
        files = {'file': f}
        res = requests.post(utils.get_url_for_loading_to_db(cluster_name, database_api_endpoint), files=files)
        try:
            response = res.json()
        except Exception:
            raise Exception("Failed to parse json response from db on loading template")
        if response['status'] != 200:
            raise Exception("Error in db! Status code: %s, msg: %s" % (response['status'], response['message']))



def translate(provider_template, validate_only, configuration_tool, cluster_name, is_delete=False,
              extra=None, log_level='info', debug=False, host_ip_parameter='public_address',
              database_api_endpoint=None, grpc_cotea_endpoint=None):
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

    template = yaml.load(provider_template, Loader=Loader)
    topology_template = template.get(TOPOLOGY_TEMPLATE)

    # tmp version - provider gets from first node template (can't use different providers in template)
    provider = None
    for key in topology_template.get('node_templates').keys():
        provider_template_name = key
        tosca_type = topology_template.get('node_templates').get(provider_template_name).get('type')
        (provider, _, _) = utils.tosca_type_parse(tosca_type)
        if provider in ['openstack', 'amazon', 'kubernetes']: # TODO: make config prividers file!
            break

    provider_config = ProviderConfiguration(provider)
    for sec in REQUIRED_CONFIGURATION_PARAMS:
        if sec not in config.get_section(config.MAIN_SECTION).keys():
            logging.error('Provider configuration parameter "%s" is missing in configuration file' % sec)
            raise Exception('Provider configuration parameter "%s" is missing in configuration file' % sec)

    def_files = config.get_section(config.MAIN_SECTION).get(TOSCA_ELEMENTS_DEFINITION_FILE)
    if isinstance(def_files, six.string_types):
        def_files = [def_files]
    provider_def_files = provider_config.get_section(config.MAIN_SECTION).get(TOSCA_ELEMENTS_DEFINITION_FILE)
    if isinstance(provider_def_files, six.string_types):
        provider_def_files = [provider_def_files]
    default_import_files = []
    for def_file in def_files:
        default_import_files.append(os.path.join(utils.get_project_root_path(), def_file))
    for def_file in provider_def_files:
        default_import_files.append(os.path.join(utils.get_project_root_path(), 'configuration_tool', 'providers',
                                                 provider, def_file))
    logging.info("Default TOSCA template definition file to be imported \'%s\'" % json.dumps(default_import_files))

    # Add default import of normative TOSCA types to the template
    template[IMPORTS] = template.get(IMPORTS, [])
    for i in range(len(template[IMPORTS])):
        if isinstance(template[IMPORTS][i], dict):
            for import_key, import_value in template[IMPORTS][i].items():
                if isinstance(import_value, six.string_types):
                    template[IMPORTS][i] = import_value
                elif isinstance(import_value, dict):
                    if import_value.get('file', None) is None:
                        logging.error("Imports %s doesn't contain \'file\' key" % import_key)
                        raise Exception("Imports %s doesn't contain \'file\' key" % import_key)
                    else:
                        template[IMPORTS][i] = import_value['file']
                    if import_value.get('repository', None) is not None:
                        logging.warning("Clouni doesn't support imports \'repository\'")
    template[IMPORTS].extend(default_import_files)
    for i in range(len(template[IMPORTS])):
        template[IMPORTS][i] = os.path.abspath(template[IMPORTS][i])

    try:
        tosca_parser_template_object = ToscaTemplate(yaml_dict_tpl=template)
    except Exception as e:
        logging.exception("Got exception from OpenStack tosca-parser: %s" % e)
        raise Exception("Got exception from OpenStack tosca-parser: %s" % e)

    # After validation, all templates are imported
    if validate_only:
        msg = 'The input "%(template_file)s" successfully passed validation. \n' \
              % {'template_file': 'TOSCA template'}
        return msg

    tosca = ProviderToscaTemplate(template, provider, configuration_tool, cluster_name,
                                  host_ip_parameter, is_delete, grpc_cotea_endpoint)

    if database_api_endpoint:
        load_to_db(tosca, config, database_api_endpoint, template, cluster_name)

    tool = get_configuration_tool_class(configuration_tool)(provider)

    default_artifacts_directory = config.get_section(config.MAIN_SECTION).get(DEFAULT_ARTIFACTS_DIRECTORY)

    configuration_content = tool.to_dsl(provider, tosca.provider_operations, tosca.reversed_provider_operations,
                                        tosca.cluster_name, is_delete, target_directory=default_artifacts_directory,
                                        inputs=tosca.inputs, outputs=tosca.outputs, extra=extra, debug=debug,
                                        grpc_cotea_endpoint=grpc_cotea_endpoint)
    return configuration_content
