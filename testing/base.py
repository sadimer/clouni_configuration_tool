from yaml import Loader

from configuration_tool.common.translator_to_configuration_dsl import translate as common_translate
import os
import yaml
import copy
import difflib

from configuration_tool.common.utils import deep_update_dict
from configuration_tool.common.tosca_reserved_keys import *

TEST = 'test'


class BaseAnsibleProvider:
    TESTING_TEMPLATE_FILENAME_TO_JOIN = ['examples', 'testing-example.yaml']
    NODE_NAME = 'tosca_server_example'

    def template_filename(self):
        r = None
        for i in self.TESTING_TEMPLATE_FILENAME_TO_JOIN:
            if r == None:
                r = i
            else:
                r = os.path.join(r, i)
        return r

    def read_template(self, filename=None):
        if not filename:
            filename = self.template_filename()
        with open(filename, 'r') as f:
            return f.read()

    def write_template(self, template, filename=None):
        if not filename:
            filename = self.template_filename()
        with open(filename, 'w') as f:
            f.write(template)

    def delete_template(self, filename=None):
        if not filename:
            filename = self.template_filename()
        if os.path.exists(filename):
            os.remove(filename)

    def parse_yaml(self, content):
        r = yaml.load(content, Loader=yaml.Loader)
        return r

    def parse_all_yaml(self, content):
        r = yaml.full_load_all(content)
        return r

    def prepare_yaml(self, content):
        r = yaml.dump(content)
        return r

    def test_provider(self):
        assert hasattr(self, 'PROVIDER') is not None
        assert self.PROVIDER in PROVIDERS

    def get_ansible_create_output(self, template, template_filename=None, extra=None, delete_template=True,
                                  host_ip_parameter='public_address', debug=True):
        if not template_filename:
            template_filename = self.template_filename()
        r = common_translate(self.prepare_yaml(template), False, ANSIBLE, TEST, is_delete=False, extra=extra,
                             log_level='debug', host_ip_parameter=host_ip_parameter, debug=debug)
        print(r)
        if delete_template:
            self.delete_template(template_filename)
        playbook = self.parse_yaml(r)
        return playbook

    def get_ansible_delete_output(self, template, template_filename=None, extra=None, delete_template=True, debug=True):
        if not template_filename:
            template_filename = self.template_filename()
        r = common_translate(self.prepare_yaml(template), False, ANSIBLE, TEST, is_delete=True, extra=extra, debug=debug)
        print(r)
        if delete_template:
            self.delete_template(template_filename)
        playbook = self.parse_yaml(r)
        return playbook

    def get_ansible_delete_output_from_file(self, template, template_filename=None, extra=None, debug=True):
        if not template_filename:
            template_filename = self.template_filename()
        r = common_translate(self.prepare_yaml(template), False, ANSIBLE, TEST, is_delete=True, extra=extra, debug=debug)
        print(r)
        playbook = self.parse_yaml(r)
        return playbook

    def get_k8s_output(self, template, template_filename=None):
        if not template_filename:
            template_filename = self.template_filename()
        r = common_translate(self.prepare_yaml(template), False, 'kubernetes', TEST, False, log_level='debug')
        print(r)
        manifest = list(self.parse_all_yaml(r))
        return manifest

    def update_node_template(self, template, node_name, update_value, param_type):
        update_value = {
            TOPOLOGY_TEMPLATE: {
                NODE_TEMPLATES: {
                    node_name: {
                        param_type: update_value
                    }
                }
            }
        }
        return deep_update_dict(template, update_value)

    def update_template_property(self, template, node_name, update_value):
        return self.update_node_template(template, node_name, update_value, PROPERTIES)

    def update_template_interfaces(self, template, node_name, update_value):
        return self.update_node_template(template, node_name, update_value, INTERFACES)

    def update_template_attribute(self, template, node_name, update_value):
        return self.update_node_template(template, node_name, update_value, PROPERTIES)

    def update_template_capability(self, template, node_name, update_value):
        return self.update_node_template(template, node_name, update_value, CAPABILITIES)

    def update_template_capability_properties(self, template, node_name, capability_name, update_value):
        uupdate_value = {
            capability_name: {
                PROPERTIES: update_value
            }
        }
        return self.update_template_capability(template, node_name, uupdate_value)

    def update_template_requirement(self, template, node_name, update_value):
        return self.update_node_template(template, node_name, update_value, REQUIREMENTS)

    def diff_files(self, file_name1, file_name2):
        with open(file_name1, 'r') as file1, open(file_name2, 'r') as file2:
            text1 = file1.readlines()
            text2 = file2.readlines()
            for line in difflib.unified_diff(text1, text2):
                print(line)


class TestAnsibleProvider(BaseAnsibleProvider):
    def test_meta(self, extra=None):
        if hasattr(self, 'check_meta'):
            with open(os.path.join('testing', 'examples', self.test_meta.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
                template = yaml.load(f, Loader=Loader)
            playbook = self.get_ansible_create_output(template, extra=extra)

            assert next(iter(playbook), {}).get('tasks')

            tasks = []
            for play in playbook:
                tasks.extend(play['tasks'])

            if extra:
                self.check_meta(tasks, testing_value="master=true", extra=extra)
            else:
                self.check_meta(tasks, testing_value="master=true")

    def test_private_address(self):
        if hasattr(self, 'check_private_address'):
            with open(os.path.join('testing', 'examples', self.test_private_address.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
                template = yaml.load(f, Loader=Loader)
            playbook = self.get_ansible_create_output(template)

            assert next(iter(playbook), {}).get('tasks')
            tasks = []
            for play in playbook:
                tasks.extend(play['tasks'])

            self.check_private_address(tasks, "192.168.12.26")

    def test_public_address(self):
        if hasattr(self, 'check_public_address'):
            with open(os.path.join('testing', 'examples', self.test_public_address.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
                template = yaml.load(f, Loader=Loader)
            playbook = self.get_ansible_create_output(template)

            assert next(iter(playbook), {}).get('tasks')

            tasks = []
            for play in playbook:
                tasks.extend(play['tasks'])
            self.check_public_address(tasks, "10.10.18.217")

    def test_network_name(self):
        if hasattr(self, 'check_network_name'):
            with open(os.path.join('testing', 'examples', self.test_network_name.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
                template = yaml.load(f, Loader=Loader)
            playbook = self.get_ansible_create_output(template)

            assert next(iter(playbook), {}).get('tasks')

            tasks = []
            for play in playbook:
                tasks.extend(play['tasks'])
            self.check_network_name(tasks, "test-two-routers")

    def test_host_capabilities(self):
        if hasattr(self, 'check_host_capabilities'):
            with open(os.path.join('testing', 'examples', self.test_host_capabilities.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
                template = yaml.load(f, Loader=Loader)
            playbook = self.get_ansible_create_output(template)

            assert next(iter(playbook), {}).get('tasks')

            tasks = []
            for play in playbook:
                tasks.extend(play['tasks'])
            self.check_host_capabilities(tasks)

    def test_endpoint_capabilities(self):
        if hasattr(self, 'check_endpoint_capabilities'):
            with open(os.path.join('testing', 'examples', self.test_endpoint_capabilities.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
                template = yaml.load(f, Loader=Loader)
            playbook = self.get_ansible_create_output(template)
            assert next(iter(playbook), {}).get('tasks')

            tasks = []
            for play in playbook:
                tasks.extend(play['tasks'])
            self.check_endpoint_capabilities(tasks)

    def test_os_capabilities(self):
        if hasattr(self, 'check_os_capabilities'):
            with open(os.path.join('testing', 'examples', self.test_os_capabilities.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
                template = yaml.load(f, Loader=Loader)
            playbook = self.get_ansible_create_output(template)
            assert next(iter(playbook), {}).get('tasks')

            tasks = []
            for play in playbook:
                tasks.extend(play['tasks'])
            self.check_os_capabilities(tasks)

    def test_scalable_capabilities(self):
        if hasattr(self, 'check_scalable_capabilities'):
            with open(os.path.join('testing', 'examples', self.test_scalable_capabilities.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
                template = yaml.load(f, Loader=Loader)
            playbook = self.get_ansible_create_output(template, extra={'tosca_server_example' + self.COMPUTE: {'with_sequence': 'start=1 end=2 format=%d'}})
            assert next(iter(playbook), {}).get('tasks')

            tasks = []
            for play in playbook:
                tasks.extend(play['tasks'])
            self.check_scalable_capabilities(tasks)

    def test_host_of_software_component(self):
        if hasattr(self, "check_host_of_software_component"):
            with open(os.path.join('testing', 'examples', self.test_host_of_software_component.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
                template = yaml.load(f, Loader=Loader)
            playbook = self.get_ansible_create_output(template)

            self.check_host_of_software_component(playbook)


    def test_get_attribute(self):
        if hasattr(self, 'check_get_attribute'):
            with open(os.path.join('testing', 'examples', self.test_get_attribute.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
                template = yaml.load(f, Loader=Loader)
            playbook = self.get_ansible_create_output(template)
            self.assertIsNotNone(next(iter(playbook), {}).get('tasks'))

            tasks = []
            for play in playbook:
                tasks.extend(play['tasks'])
            self.check_get_attribute(tasks, "master=true")

    def test_outputs(self):
        if hasattr(self, "check_outputs"):
            with open(os.path.join('testing', 'examples', self.test_outputs.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
                template = yaml.load(f, Loader=Loader)
            playbook = self.get_ansible_create_output(template)

            assert next(iter(playbook), {}).get('tasks')

            tasks = []
            for play in playbook:
                tasks.extend(play['tasks'])
            self.check_outputs(tasks, "10.10.18.217")


    def test_host_ip_parameter(self):
        if hasattr(self, "check_host_ip_parameter"):
            with open(os.path.join('testing', 'examples', self.test_host_ip_parameter.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
                template = yaml.load(f, Loader=Loader)
            playbook = self.get_ansible_create_output(template, host_ip_parameter='networks.default')

            self.check_host_ip_parameter(playbook, 'net-for-sandbox')


    def test_nodes_interfaces_operations(self):
        if hasattr(self, "check_nodes_interfaces_operations"):
            with open(os.path.join('testing', 'examples', self.test_nodes_interfaces_operations.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
                template = yaml.load(f, Loader=Loader)
            playbook = self.get_ansible_create_output(template, host_ip_parameter='public_address')

            self.check_nodes_interfaces_operations(playbook, 'test')

    def test_relationships_interfaces_operations(self):
        if hasattr(self, "check_relationships_interfaces_operations"):
            with open(os.path.join('testing', 'examples', self.test_relationships_interfaces_operations.__name__ + '_' + self.PROVIDER + '.yaml'), "r") as f:
                template = yaml.load(f, Loader=Loader)
            playbook = self.get_ansible_create_output(template, host_ip_parameter='public_address')

            self.check_relationships_interfaces_operations(playbook, 'test_relationship', 'service_1', 'test')