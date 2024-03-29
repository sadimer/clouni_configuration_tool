import json
import logging
import os
import random
import time
from threading import Thread

import grpc

from configuration_tool.common import utils
from configuration_tool.common.tosca_reserved_keys import ATTRIBUTES, OUTPUTS, PROPERTIES

from configuration_tool.configuration_tools.ansible.runner import cotea_pb2_grpc
from configuration_tool.configuration_tools.ansible.runner.cotea_pb2 import EmptyMsg, Config, MapFieldEntry, \
    Task, SessionID

SEPARATOR = '.'


def close_session(session_id, stub):
    request = SessionID()
    request.session_ID = session_id
    response = stub.StopExecution(request, timeout=1000)
    if not response.ok:
        logging.error("Can't close session with grpc cotea because of: %s", response.error_msg)
        raise Exception(response.error_msg)


def run_ansible(ansible_tasks, grpc_cotea_endpoint, extra_env, extra_vars, hosts, ansible_config=None,
                target_parameter=None, ansible_library=None, operation=None, attributes=None,
                outputs=None, properties=None):
    options = [('grpc.max_send_message_length', 100 * 1024 * 1024),
               ('grpc.max_receive_message_length', 100 * 1024 * 1024)]
    channel = grpc.insecure_channel(grpc_cotea_endpoint, options=options)
    stub = cotea_pb2_grpc.CoteaGatewayStub(channel)
    request = EmptyMsg()
    response = stub.StartSession(request, timeout=1000)
    if not response.ok:
        logging.error("Can't init session with grpc cotea because of: %s", response.error_msg)
        raise Exception(response.error_msg)
    session_id = response.ID

    request = Config()
    request.session_ID = session_id
    request.hosts = hosts
    request.inv_path = os.path.join('pb_starts', hosts + '_' + 'hosts.ini')
    request.extra_vars = str(extra_vars)
    if ansible_library:
        request.ansible_library = ansible_library
    request.not_gather_facts = False
    if hosts == 'localhost':
        request.not_gather_facts = True
    for key, val in extra_env.items():
        obj = MapFieldEntry()
        obj.key = key
        obj.value = val
        request.env_vars.add(obj)
    response = stub.InitExecution(request, timeout=1000)
    if not response.ok:
        logging.error("Can't init execution with grpc cotea because of: %s", response.error_msg)
        raise Exception(response.error_msg)
    run_result = {
        PROPERTIES: [],
        ATTRIBUTES: [],
        OUTPUTS: []
    }
    for i in range(len(ansible_tasks)):
        request = Task()
        request.session_ID = session_id
        request.is_dict = True
        request.task_str = json.dumps(ansible_tasks[i])
        response = stub.RunTask(request, timeout=1000)
        if not response.task_adding_ok:
            raise Exception(response.task_adding_error)
        for result in response.task_results:
            if result.is_unreachable or result.is_failed:
                if result.stderr != '':
                    error = result.stderr
                elif result.msg != '':
                    error = result.msg
                elif result.stdout != '':
                    error = result.stdout
                else:
                    error = result.results_dict_str
                logging.error('Task with name %s failed with exception: %s' % (result.task_name, error))
                close_session(session_id, stub)
                raise Exception('Task with name %s failed with exception: %s' % (result.task_name, error))
            else:
                if not target_parameter:
                    if operation and ansible_config:
                        if outputs:
                            run_result[OUTPUTS].extend(get_outputs(ansible_tasks[i], result, outputs, ansible_config,
                                                              operation))
                        if attributes:
                            run_result[ATTRIBUTES].extend(get_attributes_or_properties(ansible_tasks[i], result, attributes,
                                                                                  ansible_config,
                                                                                  operation))
                        if properties:
                            run_result[PROPERTIES].extend(get_attributes_or_properties(ansible_tasks[i], result, properties,
                                                                                  ansible_config,
                                                                                  operation))
            if target_parameter:
                result = json.loads(result.results_dict_str)
                if 'ansible_facts' in result and target_parameter.split('.')[-1] in result['ansible_facts']:
                    run_result = result['ansible_facts'][target_parameter.split('.')[-1]]
    close_session(session_id, stub)
    return run_result


def get_outputs(task, result, parameters, ansible_config, operation):
    run_result = []
    tmp = {}
    if 'include' in task.keys() and json.loads(result.results_dict_str).get('ansible_facts'):
        operation_facts = json.loads(result.results_dict_str).get('ansible_facts')
        for output in parameters:
            if operation_facts.get(output):
                tmp[output] = operation_facts.get(output)
        if len(tmp) > 0:
            run_result.append(tmp)
    return run_result


def get_attributes_or_properties(task, result, parameters, ansible_config, operation):
    run_result = []
    tmp = {}
    if 'include' in task.keys() and json.loads(result.results_dict_str).get('ansible_facts'):
        operation_facts = json.loads(result.results_dict_str).get('ansible_facts')
        for par in parameters:
            if operation_facts.get(par):
                tmp[par] = operation_facts.get(par)
        if len(tmp) > 0:
            run_result.append(tmp)
    if ansible_config.get('module_description' + '_' + operation.lower()) and \
            ansible_config.get('module_description' + '_' + operation.lower()) in result.task_name:
        attribute_matcher = None
        for elem in task.keys():
            if ansible_config.get('module_prefix') in elem:
                attribute_matcher = elem
        module_attribute_matcher = ansible_config.get('module_attribute_matcher')
        if attribute_matcher in module_attribute_matcher:
            attribute_matcher = module_attribute_matcher.get(attribute_matcher)
        else:
            attribute_matcher = attribute_matcher.replace(ansible_config.get('module_prefix'), '')
        operation_output = json.loads(result.results_dict_str).get(attribute_matcher)
        operation_output_results = json.loads(result.results_dict_str).get('results')
        if operation_output:
            tmp = {}
            for par in parameters:
                if operation_output.get(par):
                    tmp[par] = operation_output.get(par)
            if len(tmp) > 0:
                run_result.append(tmp)
        if operation_output_results:
            for elem in operation_output_results:
                match = elem.get(attribute_matcher)
                tmp = {}
                if match:
                    for par in parameters:
                        if match.get(par):
                            tmp[par] = match.get(par)
                    if len(tmp) > 0:
                        run_result.append(tmp)
    return run_result


def run_and_finish(ansible_tasks, grpc_cotea_endpoint, extra_env, extra_vars, hosts, name, op, q, ansible_config,
                   ansible_library, attributes, outputs, properties):
    result = {}
    # time.sleep(random.randint(0, 30))
    try:
        result = run_ansible(ansible_tasks, grpc_cotea_endpoint, extra_env, extra_vars, hosts,
                             ansible_config=ansible_config, ansible_library=ansible_library, operation=op,
                             attributes=attributes, outputs=outputs, properties=properties)
    except Exception as e:
        q.put(e)
    q.put({name + SEPARATOR + op: result})


def grpc_cotea_run_ansible(ansible_tasks, grpc_cotea_endpoint, extra_env, extra_vars, hosts, name, op, q,
                           ansible_config, ansible_library=None, attributes=None, outputs=None, properties=None):
    Thread(target=run_and_finish, args=(
        ansible_tasks, grpc_cotea_endpoint, extra_env, extra_vars, hosts, name, op, q, ansible_config, ansible_library,
        attributes, outputs, properties)).start()
