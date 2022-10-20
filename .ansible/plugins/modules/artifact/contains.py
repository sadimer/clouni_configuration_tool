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
module: contains
short_description: implementation of contains artifact
author:
    - Roman Stolyarov (Sadimer)
'''

EXAMPLES = '''
'''

RETURN = '''
'''

try:
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
    if len(ansible_module.params['input_args']) != 2:
        ansible_module.fail_json(msg="Bad input args, should use 0 arg for match parameter and 1 arg for match value")
    match_params = ansible_module.params['input_args'][0]
    if not isinstance(match_params, list):
        ansible_module.fail_json(msg="Bad input args, 0 arg should be a list with possible keys")
    match_val = ansible_module.params['input_args'][1]
    match_vals = []
    if isinstance(match_val, dict):
        for k, v in match_val.items():
            match_vals += [str(v)]
    elif isinstance(match_val, list):
        for v in match_val:
            match_vals += [str(v)]
    else:
        match_vals = [str(match_val)]
    results = []
    for elem in input_facts:
        for match_param in match_params:
            choice = str(elem.get(match_param)).lower()
            flag = True
            for val in match_vals:
                if choice.find(val.lower()) == -1:
                    flag = False
                    break
            if flag:
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
