import logging
import os
from threading import Thread

import grpc
import yaml

from configuration_tool.common import utils
from configuration_tool.common.utils import get_random_int


import ast

from configuration_tool.configuration_tools.ansible import cotea_pb2_grpc
from configuration_tool.configuration_tools.ansible.cotea_pb2 import StartSessionMSG, EmptyMsg, Config, MapFieldEntry, \
    Task, SessionID

SEPARATOR = '.'


def close_session(session_id, stub):
    request = SessionID()
    request.session_ID = session_id
    response = stub.StopExecution(request)
    if not response.ok:
        logging.error("Can't close session with grpc cotea because of: %s", response.error_msg)
        raise Exception(response.error_msg)


def run_ansible(ansible_tasks, grpc_cotea_endpoint, extra_env, extra_vars):
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
    for key, val in extra_vars.items():
        obj = MapFieldEntry()
        obj.key = key
        obj.value = val
        request.extra_vars.add(obj)
    for key, val in extra_env.items():
        obj = MapFieldEntry()
        obj.key = key
        obj.value = val
        request.env_vars.add(obj)
    response = stub.InitExecution(request)
    if not response.ok:
        logging.error("Can't init execution with grpc cotea because of: %s", response.error_msg)
        raise Exception(response.error_msg)
    for task in ansible_tasks:
        request = Task()
        request.session_ID = session_id
        request.task_str = task
        response = stub.RunTask(request)
        if not response.task_adding_ok:
            raise Exception(response.task_adding_error)
        for result in response.task_results:
            if result.is_unreachable or result.is_failed:
                logging.error('Task with name %s failed with exception: %s' % (result.task_name, result.stderr))
                close_session(session_id, stub)
                raise Exception('Task with name %s failed with exception: %s' % (result.task_name, result.stderr))
    close_session(session_id, stub)
    return session_id


def run_and_finish(ansible_tasks, grpc_cotea_endpoint, extra_env, extra_vars, name, op, q):
    results = run_ansible(ansible_tasks, grpc_cotea_endpoint, extra_env, extra_vars)
    if name is not None and op is not None:
        q.put(results)
    else:
        q.put(name + SEPARATOR + op)


def grpc_cotea_run_ansible(ansible_tasks, grpc_cotea_endpoint, extra_env, extra_vars, name, op, q):
    Thread(target=run_and_finish, args=(ansible_tasks, grpc_cotea_endpoint, extra_env, extra_vars, name, op, q)).start()



