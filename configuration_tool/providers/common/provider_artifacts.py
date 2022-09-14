import copy
import sys
import logging
from multiprocessing import Queue

from random import seed, randint
from time import time

from configuration_tool.common.configuration import Configuration
from configuration_tool.configuration_tools.ansible.runner.runner import grpc_cotea_run_ansible

ARTIFACT_RANGE_START = 1000
ARTIFACT_RANGE_END = 9999

REGISTER = 'register'

from configuration_tool.common import utils

from configuration_tool.common.tosca_reserved_keys import PARAMETERS, VALUE, EXTRA, SOURCE, DEFAULT_ARTIFACTS_DIRECTORY, \
    ANSIBLE
import yaml
import os


def generate_artifacts(executor, new_artifacts, directory, store=True):
    """
    From the info of new artifacts generate files which execute
    :param new_artifacts: list of dicts containing (value, source, parameters, executor, name, configuration_tool)
    :return: None
    """
    if not executor:
        logging.error('Failed to generate artifact with executor <None>')
        raise Exception('Failed to generate artifact with executor <None>')
    tasks = []
    filename = os.path.join(directory, '_'.join(['tasks', str(utils.get_random_int(1000, 9999))]) +
                            get_artifact_extension(executor))
    for art in new_artifacts:
        tasks.extend(create_artifact_data(art, executor))
    if not os.path.isdir(directory):
        os.makedirs(directory)

    with open(filename, "w") as f:
        filedata = yaml.dump(tasks, default_flow_style=False, sort_keys=False)
        f.write(filedata)
        logging.info("Artifact for executor %s was created: %s" % (executor, filename))

    return tasks, filename

def create_artifact_data(data, executor):
    if executor == ANSIBLE:
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


def create_artifact(filename, data, executor):
    if executor == ANSIBLE:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        tasks = create_artifact_data(data)
        with open(filename, "w") as f:
            filedata = yaml.dump(tasks, default_flow_style=False, sort_keys=False)
            f.write(filedata)
            logging.info("Artifact for executor %s was created: %s" % (executor, filename))


def get_artifact_extension(executor):
    return '.yaml'


def get_initial_artifacts_directory():
    config = Configuration()
    return config.get_section(config.MAIN_SECTION).get(DEFAULT_ARTIFACTS_DIRECTORY)


def execute(new_global_elements_map_total_implementation, is_delete, target_parameter=None, grpc_cotea_endpoint=None):
    if not is_delete:
        default_executor = ANSIBLE
        new_ansible_artifacts = copy.deepcopy(new_global_elements_map_total_implementation)
        for i in range(len(new_ansible_artifacts)):
            new_ansible_artifacts[i]['configuration_tool'] = new_ansible_artifacts[i]['executor']
            extension = get_artifact_extension(new_ansible_artifacts[i]['executor'])

            seed(time())
            new_ansible_artifacts[i]['name'] = '_'.join(
                [SOURCE, str(randint(ARTIFACT_RANGE_START, ARTIFACT_RANGE_END))]) + extension
        artifacts_with_brackets = utils.replace_brackets(new_ansible_artifacts, False)
        new_ansible_tasks, filename = generate_artifacts(default_executor, artifacts_with_brackets,
                                                                   get_initial_artifacts_directory(),
                                                                   store=False)
        os.remove(filename)
        q = Queue()
        if grpc_cotea_endpoint:
            grpc_cotea_run_ansible(new_ansible_tasks, grpc_cotea_endpoint, {}, {}, None, None, q)
            results = q.get()
            if target_parameter is not None:
                value = None
                if_failed = False
                for result in results:
                    if result.is_failed or result.is_unreachable:
                        logging.error("Task %s has failed because of exception: \n%s" %
                                      (result.task_name, result.result.get('exception', '(Unknown reason)')))
                        if_failed = True
                    if 'results' in result.result and len(result.result['results']) > 0 and 'ansible_facts' in \
                            result.result['results'][0] and 'matched_object' in result.result['results'][0][
                        'ansible_facts']:
                        value = result.result['results'][0]['ansible_facts']['matched_object'][
                            target_parameter.split('.')[-1]]
                if if_failed:
                    value = None
                return value
    return None