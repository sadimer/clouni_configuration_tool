import copy
import logging

import yaml
from yaml import Loader

from configuration_tool.common import utils
from configuration_tool.common.tosca_reserved_keys import NODES, NODE_TYPES, ATTRIBUTES, NODE_TEMPLATES, NAME, \
    RELATIONSHIP_TEMPLATES, RELATIONSHIPS


def update_instance_model(cluster_name, tmpl, type, name, attributes, delete, init=False):
    with open('instance_model_' + cluster_name + '.yaml', 'a+') as instance_model:
        curr_state = {}
        if not delete:
            (_, tosca_type, _) = utils.tosca_type_parse(type)
            if tosca_type == NODES:
                if NODE_TEMPLATES not in curr_state:
                    curr_state[NODE_TEMPLATES] = {}
                    elem_type = NODE_TEMPLATES
            elif tosca_type == RELATIONSHIPS:
                if RELATIONSHIP_TEMPLATES not in curr_state:
                    curr_state[RELATIONSHIP_TEMPLATES] = {}
                    elem_type = RELATIONSHIP_TEMPLATES
            else:
                logging.error('Unknown tosca type: %s' % type)
                raise Exception('Unknown tosca type: %s' % type)
            if init:
                name = name + '_' + str(1)
                curr_state[elem_type][name] = copy.deepcopy(tmpl)
                print(yaml.dump([curr_state]), file=instance_model)
                return
            for i in range(len(attributes)):
                name = name + '_' + str(i + 1)
                # temporary solution
                with open('instance_model_' + cluster_name + '.yaml', 'r+') as instance_model_read:
                    old_state = yaml.load(instance_model_read, Loader=Loader)
                    for elem in old_state[::-1]:
                        if elem.get(elem_type) and name in elem.get(elem_type):
                            tmpl = elem[elem_type][name]
                            break
                curr_state[elem_type][name] = utils.deep_update_dict(copy.deepcopy(tmpl),
                                                                            {ATTRIBUTES: attributes[i]})
                print(yaml.dump([curr_state]), file=instance_model)


def get_actual_state_of_instance_model(cluster_name, name):
    name = name + '_' + '1' # temporary solution
    with open('instance_model_' + cluster_name + '.yaml', 'r+') as instance_model:
        curr_state = yaml.load(instance_model, Loader=Loader)
        for elem in curr_state[::-1]:
            if elem.get(NODE_TEMPLATES) and name in elem.get(NODE_TEMPLATES):
                return elem[NODE_TEMPLATES][name], NODES
            if elem.get(RELATIONSHIP_TEMPLATES) and name in elem.get(RELATIONSHIP_TEMPLATES):
                return elem[RELATIONSHIP_TEMPLATES][name], RELATIONSHIPS
    logging.error('Node or relationship with name %s does not exists' % name)
    raise Exception('Node or relationship with name %s does not exists' % name)
