import logging
import os
from threading import Thread

import grpc
import yaml

from configuration_tool.common import utils
from configuration_tool.common.utils import get_random_int


import ast

from configuration_tool.configuration_tools.ansible import cotea_pb2_grpc
from configuration_tool.configuration_tools.ansible.cotea_pb2 import StartSessionMSG, EmptyMsg, Config, MapFieldEntry

SEPARATOR = '.'


def run_ansible(ansible_playbook, session_id):
    """

    :param ansible_playbook: dict which is equal to Ansible playbook in YAML
    :param cluster_name: name of cluster
    :return: empty
    """
    # TODO feature with task management
    pass


def run_and_finish(ansible_playbook, name, op, q, cluster_name):
    results = run_ansible(ansible_playbook, cluster_name)
    if name is not None and op is not None:
        if name == 'artifacts' or op == 'artifacts':
            q.put(results)
        else:
            q.put(name + SEPARATOR + op)
    else:
        q.put('Done')


def grpc_cotea_run_ansible(ansible_playbook, name, op, q, grpc_cotea_endpoint):
    channel = grpc.insecure_channel(grpc_cotea_endpoint)
    stub = cotea_pb2_grpc.AnsibleExecutorStub(channel)
    request = EmptyMsg()
    response = stub.StartSession(request)
    if not response.ok:
        logging.error("Can't init session with grpc cotea because of: %s", response.error_msg)
        raise Exception(response.error_msg)
    session_id = response.ID

    request = Config()
    request.session_ID = session_id
    tmp_current_dir = utils.get_tmp_clouni_dir()
    request.pb_path = tmp_current_dir
    request.inv_path = os.path.join(tmp_current_dir, 'hosts.ini')
    request.emty_task_name = 'Init'
    obj = MapFieldEntry()
    request.extra_vars.add(obj)
    obj = MapFieldEntry()
    request.env_vars.add(obj)
    response = stub.InitExecution(request)
    if not response.ok:
        logging.error("Can't init execution with grpc cotea because of: %s", response.error_msg)
        raise Exception(response.error_msg)

    Thread(target=run_and_finish, args=(ansible_playbook, name, op, q, session_id)).start()



