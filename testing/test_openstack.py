import unittest

import six
from yaml import Loader

from testing.base import TestAnsibleProvider

from configuration_tool.common.tosca_reserved_keys import *

import copy, os, re, yaml

from configuration_tool.common import utils

SERVER_MODULE_NAME = 'os_server'
PORT_MODULE_NAME = 'os_port'
FIP_MODULE_NAME = 'os_floating_ip'
SEC_GROUP_MODULE_NAME = 'os_security_group'
SEC_RULE_MODULE_NAME = 'os_security_group_rule'
NETWORK_MODULE_NAME = 'os_network'
SUBNET_MODULE_NAME = 'os_subnet'

SUCCESS_CHECK_FILE = 'successful_tasks.yaml'

class TestAnsibleOpenStackOutput (unittest.TestCase, TestAnsibleProvider):
    PROVIDER = 'openstack'
    COMPUTE = '_server'
    SEC_GROUP = '_security_group'
    SEC_GROUP_RULE = '_security_group_rule'
    SUBNET = '_subnet'
    NETWORK = '_network'
    PORT = '_port'
    FIP = '_floating_ip'

    def test_server_name(self):
        with open(os.path.join('testing', 'examples', self.test_server_name.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
            template = yaml.load(f, Loader=Loader)
        playbook = self.get_ansible_create_output(template)
        self.assertEqual(len(playbook), 2)
        for play in playbook:
            self.assertIsInstance(play, dict)
            self.assertIsNotNone(play['tasks'])
        tasks = []
        for play in playbook:
            for task in play['tasks']:
                tasks.append(task)
        self.assertEqual(len(tasks), 6)
        self.assertIsNotNone(tasks[1][SERVER_MODULE_NAME])
        server = tasks[1][SERVER_MODULE_NAME]
        self.assertEqual(server['name'], self.NODE_NAME)

    def test_meta(self, extra=None):
        super(TestAnsibleOpenStackOutput, self).test_meta(extra=extra)

    def check_meta (self, tasks, testing_value=None, extra=None):
        server_name = None
        for task in tasks:
            if task.get(SERVER_MODULE_NAME):
                self.assertIsNotNone(task[SERVER_MODULE_NAME].get('name'))
                self.assertIsNotNone(task[SERVER_MODULE_NAME].get('meta'))
                server_name = task[SERVER_MODULE_NAME]['name']
                server_meta = task[SERVER_MODULE_NAME]['meta']
                if testing_value:
                    self.assertEqual(server_meta, testing_value)
        self.assertIsNotNone(server_name)

    def test_private_address(self):
        super(TestAnsibleOpenStackOutput, self).test_private_address()

    def check_private_address(self, tasks, testing_value=None):
        port_name = None
        server_nics = None
        for task in tasks:
            if task.get(PORT_MODULE_NAME):
                self.assertIsNotNone(task[PORT_MODULE_NAME].get("name"))
                self.assertIsNotNone(task[PORT_MODULE_NAME].get("fixed_ips"))
                port_name = task[PORT_MODULE_NAME]['name']
            if task.get(SERVER_MODULE_NAME):
                self.assertIsNotNone(task[SERVER_MODULE_NAME].get('nics'))
                server_nics = task[SERVER_MODULE_NAME]['nics']
                self.assertIsNotNone(port_name)
                port_found = False
                for nic_info in server_nics:
                    if nic_info.get('port-name', '') == port_name:
                        port_found = True
                        break
                self.assertTrue(port_found)
        self.assertIsNotNone(server_nics)

    def test_public_address(self):
        super(TestAnsibleOpenStackOutput, self).test_public_address()

    def check_public_address(self, tasks, testing_value=None):
        fip_server = None
        server_name = None
        for task in tasks:
            if task.get(FIP_MODULE_NAME):
                self.assertIsNotNone(task[FIP_MODULE_NAME].get("network"))
                self.assertIsNotNone(task[FIP_MODULE_NAME].get("floating_ip_address"))
                self.assertIsNotNone(task[FIP_MODULE_NAME].get("server"))
                fip_server = task[FIP_MODULE_NAME]['server']
                self.assertIsNotNone(server_name)
                self.assertEqual(fip_server, server_name)
                floating_ip = task[FIP_MODULE_NAME]['floating_ip_address']
                self.assertEqual(floating_ip, testing_value)
            if task.get(SERVER_MODULE_NAME):
                self.assertIsNotNone(task[SERVER_MODULE_NAME].get('name'))
                server_name = task[SERVER_MODULE_NAME]['name']
        self.assertIsNotNone(fip_server)

    def test_network_name(self):
        super(TestAnsibleOpenStackOutput, self).test_network_name()

    def check_network_name(self, tasks, testing_value=None):
        server_name = None
        for task in tasks:
            if task.get(SERVER_MODULE_NAME):
                self.assertIsNotNone(task[SERVER_MODULE_NAME].get('name'))
                self.assertIsNotNone(task[SERVER_MODULE_NAME].get('nics'))
                server_name = task[SERVER_MODULE_NAME]['name']
                server_nics = task[SERVER_MODULE_NAME]['nics']
                if testing_value:
                    nic_found = False
                    for nic_info in server_nics:
                        if nic_info.get('net-name', '') == testing_value:
                            nic_found = True
                            break
                    self.assertTrue(nic_found)
        self.assertIsNotNone(server_name)

    def test_host_capabilities(self):
        super(TestAnsibleOpenStackOutput, self).test_host_capabilities()

    def check_host_capabilities(self, tasks, testing_value=None):
        server_name = None
        for task in tasks:
            if task.get(SERVER_MODULE_NAME):
                self.assertIsNotNone(task[SERVER_MODULE_NAME].get('flavor'))
                self.assertIsNotNone(task[SERVER_MODULE_NAME].get('name'))
                server_name = task[SERVER_MODULE_NAME]['name']
                server_flavor = task[SERVER_MODULE_NAME]['flavor']
                if testing_value:
                    self.assertEqual(server_flavor, testing_value)
        self.assertIsNotNone(server_name)

    def test_endpoint_capabilities(self):
        super(TestAnsibleOpenStackOutput, self).test_endpoint_capabilities()

    def check_endpoint_capabilities(self, tasks, testing_value=None):
        sec_group_name = None
        if_sec_rule = False
        server_name = None
        for task in tasks:
            if task.get(SEC_GROUP_MODULE_NAME):
                self.assertIsNotNone(task[SEC_GROUP_MODULE_NAME].get('name'))
                sec_group_name = task[SEC_GROUP_MODULE_NAME]['name']
                self.assertFalse(if_sec_rule)
                self.assertIsNone(server_name)
            if task.get(SEC_RULE_MODULE_NAME):
                self.assertIsNotNone(task[SEC_RULE_MODULE_NAME].get('direction'))
                self.assertIsNotNone(task[SEC_RULE_MODULE_NAME].get('port_range_min'))
                self.assertIsNotNone(task[SEC_RULE_MODULE_NAME].get('port_range_max'))
                self.assertIsNotNone(task[SEC_RULE_MODULE_NAME].get('protocol'))
                self.assertIsNotNone(task[SEC_RULE_MODULE_NAME].get('remote_ip_prefix'))
                self.assertIsNotNone(task[SEC_RULE_MODULE_NAME].get('security_group'))
                if_sec_rule = True
                self.assertIsNotNone(sec_group_name)
                rule_group_name = task[SEC_RULE_MODULE_NAME]['security_group']
                self.assertEqual(rule_group_name, sec_group_name)
                # self.assertIsNone(server_name)
            if task.get(SERVER_MODULE_NAME):
                self.assertIsNotNone(task[SERVER_MODULE_NAME].get('name'))
                self.assertIsNotNone(task[SERVER_MODULE_NAME].get('security_groups'))
                server_name = task[SERVER_MODULE_NAME]['name']
                server_groups = task[SERVER_MODULE_NAME]['security_groups']
                self.assertIsNotNone(sec_group_name)
                self.assertIn(sec_group_name, server_groups)
        self.assertIsNotNone(server_name)

    def test_os_capabilities(self):
        super(TestAnsibleOpenStackOutput, self).test_os_capabilities()

    def check_os_capabilities(self, tasks, testing_value=None):
        server_name = None
        for task in tasks:
            if task.get(SERVER_MODULE_NAME):
                self.assertIsNotNone(task[SERVER_MODULE_NAME].get('image'))
                self.assertIsNotNone(task[SERVER_MODULE_NAME].get('name'))
                server_name = task[SERVER_MODULE_NAME]['name']
                server_image = task[SERVER_MODULE_NAME]['image']
                if testing_value:
                    self.assertEqual(testing_value, server_image)
        self.assertIsNotNone(server_name)

    def test_multiple_relationships(self):
        with open(os.path.join('testing', 'examples', self.test_multiple_relationships.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
            template = yaml.load(f, Loader=Loader)
        playbook = self.get_ansible_create_output(template)

        self.assertIsNotNone(next(iter(playbook), {}).get('tasks'))

        tasks = []
        for play in playbook:
            for task in play['tasks']:
                tasks.append(task)
        self.check_public_address(tasks, "10.100.115.15")
        self.check_private_address(tasks, "192.168.12.25")

    def test_scalable_capabilities(self):
        super(TestAnsibleOpenStackOutput, self).test_scalable_capabilities()

    def check_scalable_capabilities(self, tasks, testing_value=None):
        server_name = None
        default_instances = 2
        if testing_value:
            default_instances = testing_value.get('default_instances', default_instances)
        for task in tasks:
            if task.get(SERVER_MODULE_NAME):
                self.assertIsNotNone(task.get('with_sequence'))
                self.assertIsNotNone(task[SERVER_MODULE_NAME].get('name'))
                self.assertTrue(task['with_sequence'], 'start=1 end=' + str(default_instances) + ' format=')
                server_name = task[SERVER_MODULE_NAME]['name']
        self.assertIsNotNone(server_name)

    def test_host_of_software_component(self):
        super(TestAnsibleOpenStackOutput, self).test_host_of_software_component()

    def check_host_of_software_component(self, playbook):
        self.assertEqual(len(playbook), 4)
        for play in playbook:
            self.assertIsNotNone(play.get('tasks'))

        self.assertEqual(playbook[3].get('hosts'), self.NODE_NAME + '_server_public_address')
        tasks2 = playbook[3]['tasks']
        tasks1 = playbook[0]['tasks'] + playbook[1]['tasks'] + playbook[2]['tasks']
        tasks = tasks1
        checked = False
        for i in range(len(tasks)):
            if tasks[i].get('os_floating_ip', None) != None:
                fip_var = tasks[i]['register']

                self.assertIsNotNone(tasks[i + 1].get('set_fact', {}).get('ansible_user'))
                self.assertEqual(tasks[i + 2].get('set_fact', {}).get('group'), self.NODE_NAME + '_server_public_address')
                self.assertIsNotNone(tasks[i + 3].get('set_fact', None))
                self.assertEqual(tasks[i + 3]['set_fact'].get('host_ip', None),
                                 '{{ host_ip | default([]) + [[ "tosca_server_example_public_address_" + item, ' +
                                 fip_var + '.results[item | int - 1].floating_ip.floating_ip_address ]] }}')
                self.assertIsNotNone(tasks[i + 4].get('include', None))
                self.assertEqual(tasks[i + 4]['include'], '/tmp/clouni/artifacts/add_host.yaml')
                checked = True
        self.assertTrue(checked)

        tasks = tasks2
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].get('set_fact', {}).get('version', None), 0.1)
        self.assertEqual(tasks[1].get('include', None), "/tmp/clouni/artifacts/testing/examples/ansible-server-example.yaml")

    def test_host_ip_parameter(self):
        super(TestAnsibleOpenStackOutput, self).test_host_ip_parameter()

    def check_host_ip_parameter(self, playbook, testing_value):
        self.assertEqual(len(playbook), 4)
        for play in playbook:
            self.assertIsNotNone(play.get('tasks'))
            self.assertEqual(play.get('hosts'), 'localhost')

        tasks = []
        for play in playbook:
            tasks.extend(play['tasks'])
        for i in range(len(tasks)):
            if tasks[i].get('os_server'):
                self.assertEqual(tasks[i]['os_server']['nics'][0]['net-name'], testing_value)
                ansible_user = tasks[i + 1].get('set_fact', {}).get('ansible_user')
                group = tasks[i + 2].get('set_fact', {}).get('group')
                host_ip = tasks[i + 3].get('set_fact', {}).get('host_ip')
                include = tasks[i + 4].get('include')
                self.assertEqual(ansible_user, 'cirros')
                self.assertEqual(host_ip, '{{ host_ip | default([]) + [[ "tosca_server_example_private_address_" + item, tosca_server_example_server.results[item | int - 1].server.public_v4 ]] }}')
                self.assertEqual(group, 'tosca_server_example_server_private_address')
                self.assertEqual(include, '/tmp/clouni/artifacts/add_host.yaml')


    def test_nodes_interfaces_operations(self):
        super(TestAnsibleOpenStackOutput, self).test_nodes_interfaces_operations()

    def check_nodes_interfaces_operations(self, plays, testing_value):
        self.assertEqual(len(plays), 6)

        for play in plays:
            self.assertIsNotNone(play.get('tasks'))
            self.assertEqual(play.get('hosts'), 'localhost')

        self.assertTrue('create' in plays[1].get('name'))
        self.assertTrue('configure' in plays[2].get('name'))
        self.assertTrue('start' in plays[3].get('name'))
        self.assertTrue('stop' in plays[4].get('name'))

        checked = False
        for task in plays[1]['tasks']:
            if task.get('os_server'):
                checked = True
        self.assertTrue(checked)

        for i in range(2, 5):
            self.assertEqual(plays[i]['tasks'][0].get('set_fact', {}).get(testing_value), testing_value)
            self.assertEqual(plays[i]['tasks'][1].get('include'), '/tmp/clouni/artifacts/testing/examples/ansible-operation-example.yaml')

        checked = False
        for task in plays[5]['tasks']:
            if task.get('os_floating_ip'):
                checked = True
        self.assertTrue(checked)

    def test_relationships_interfaces_operations(self):
        super(TestAnsibleOpenStackOutput, self).test_relationships_interfaces_operations()

    def check_relationships_interfaces_operations(self, plays, rel_name, soft_name, testing_value):
        self.assertEqual(len(plays), 11)
        for play in plays:
            self.assertIsNotNone(play.get('tasks'))

        self.assertTrue('create' in plays[0].get('name'))
        self.assertTrue('create' in plays[1].get('name'))

        checked = False
        for task in plays[1]['tasks']:
            if task.get('os_server'):
                checked = True
        self.assertTrue(checked)

        self.assertTrue('pre_configure_target' in plays[2].get('name'))
        self.assertTrue(rel_name + '_hosted_on' in plays[2].get('name'))
        self.assertEqual(plays[2].get('hosts'), 'localhost')

        self.assertTrue('configure' in plays[3].get('name'))

        self.assertTrue('post_configure_target' in plays[4].get('name'))
        self.assertTrue(rel_name + '_hosted_on' in plays[4].get('name'))
        self.assertEqual(plays[4].get('hosts'), 'localhost')

        checked = False
        for task in plays[5]['tasks']:
            if task.get('os_floating_ip'):
                checked = True
        self.assertTrue(checked)

        self.assertTrue('create' in plays[6].get('name'))
        self.assertTrue(soft_name + '_server_example' in plays[6].get('name'))

        if 'pre_configure_source' in plays[7].get('name'):
            self.assertTrue(rel_name+ '_hosted_on' in plays[7].get('name'))
            self.assertEqual(plays[7].get('hosts'), 'tosca_server_example_server_public_address')

            self.assertTrue('add_source' in plays[8].get('name'))
            self.assertTrue(rel_name + '_hosted_on' in plays[8].get('name'))
            self.assertEqual(plays[8].get('hosts'), 'localhost')
        elif 'add_source' in plays[7].get('name'):
            self.assertTrue(rel_name + '_hosted_on' in plays[7].get('name'))
            self.assertEqual(plays[7].get('hosts'), 'localhost')

            self.assertTrue('pre_configure_source' in plays[8].get('name'))
            self.assertTrue(rel_name + '_hosted_on' in plays[8].get('name'))
            self.assertEqual(plays[8].get('hosts'), 'tosca_server_example_server_public_address')
        else:
            self.assertTrue(False)

        self.assertTrue('configure' in plays[9].get('name'))
        self.assertTrue(soft_name+ '_server_example' in plays[9].get('name'))

        self.assertTrue('post_configure_source' in plays[10].get('name'))
        self.assertTrue(rel_name + '_hosted_on' in plays[10].get('name'))
        self.assertEqual(plays[10].get('hosts'), 'tosca_server_example_server_public_address')


        for i in list(range(2, 5)) + list(range(6, 11)):
            self.assertEqual(plays[i]['tasks'][0].get('set_fact', {}).get(testing_value), testing_value)
            self.assertEqual(plays[i]['tasks'][1].get('include'), '/tmp/clouni/artifacts/testing/examples/ansible-operation-example.yaml')
