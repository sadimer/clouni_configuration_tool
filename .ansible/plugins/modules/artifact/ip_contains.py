#!/usr/bin/python
# Copyright: Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

ANSIBLE_METADATA = {'metadata_version': '1.1',
                    'status': ['preview'],
                    'supported_by': 'community'}

DOCUMENTATION = '''
---
module: ip contains
short_description: implementation of ip contains artifact
author:
    - Roman Stolyarov (Sadimer)
requirements: [ "ipaddress" ]
'''

EXAMPLES = '''
'''

RETURN = '''
'''

try:
    from ipaddress import IPv4Address

    HAS_LIB = True
except:
    HAS_LIB = False

from ansible.module_utils.basic import AnsibleModule


def main():
    argument_spec = dict(
        input_facts=dict(type='list', required=True),
        input_args=dict(type='list', required=True),
    )
    ansible_module = AnsibleModule(argument_spec=argument_spec, supports_check_mode=True)
    input_facts = ansible_module.params['input_facts']
    if len(ansible_module.params['input_args']) != 3:
        ansible_module.fail_json(msg="Bad input args, should use 0 arg for match parameter and 1 arg for match value")
    param_start = ansible_module.params['input_args'][0]
    param_end = ansible_module.params['input_args'][1]
    address = ansible_module.params['input_args'][2]
    results = []
    for elem in input_facts:
        if IPv4Address(address) >= IPv4Address(elem.get(param_start)) and \
                IPv4Address(elem.get(param_end)) >= IPv4Address(address):
            results.append(elem)
    if len(results) == 0:
        ansible_module.fail_json(msg="There are no matchable objects")
    elif len(results) > 1:
        ansible_module.warn(warning="WARNING: there are more than one matchable objects")
        ansible_module.exit_json(matched_object=results[-1])
    else:
        ansible_module.exit_json(matched_object=results[-1])


if __name__ == '__main__':
    main()
