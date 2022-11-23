import copy
import logging
import os

import yaml
from yaml import Loader

from configuration_tool.common import utils
from configuration_tool.common.tosca_reserved_keys import NODES, NODE_TYPES, ATTRIBUTES, NODE_TEMPLATES, NAME, \
    RELATIONSHIP_TEMPLATES, RELATIONSHIPS, PROPERTIES


def update_instance_model(cluster_name, tmpl, type, name, attributes, properties, delete, init=False):
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
                print(yaml.dump([curr_state]), file=instance_model, flush=True)
                return
            update_attributes_or_properties(cluster_name, name, curr_state, elem_type,
                                            attributes, ATTRIBUTES, instance_model)
            update_attributes_or_properties(cluster_name, name, curr_state, elem_type,
                                            properties, PROPERTIES, instance_model)


def update_attributes_or_properties(cluster_name, name, curr_state, elem_type, parameters, parameter_type,
                                    instance_model):
    for i in range(len(parameters)):
        tmpl = get_actual_state_of_instance_model(cluster_name, name, i + 1, init=True)
        if not tmpl:
            logging.error('Cant get actual state of template with name: %s' % name)
            raise Exception('Cant get actual state of template with name: %s' % name)
        curr_state[elem_type][name + '_' + str(i + 1)] = utils.deep_update_dict(copy.deepcopy(tmpl),
                                                                                {parameter_type: parameters[i]})
        print(yaml.dump([curr_state], Dumper=utils.NoAliasDumper), file=instance_model, flush=True)


def get_elem(curr_state, name):
    for elem in curr_state[::-1]:
        if elem.get(NODE_TEMPLATES) and name in elem.get(NODE_TEMPLATES):
            return elem[NODE_TEMPLATES][name]
        if elem.get(RELATIONSHIP_TEMPLATES) and name in elem.get(RELATIONSHIP_TEMPLATES):
            return elem[RELATIONSHIP_TEMPLATES][name]
    return None


def get_actual_state_of_instance_model(cluster_name, name, index, init=False):
    with open('instance_model_' + cluster_name + '.yaml', 'r+') as instance_model:
        curr_state = yaml.load(instance_model, Loader=Loader)
        elem = get_elem(curr_state, name + '_' + str(index))
        if elem:
            return elem
        if init:
            # if can't find elem in template with index - get from 1 index
            return get_elem(curr_state, name + '_' + '1')
        return None


def delete_cluster_from_instance_model(cluster_name):
    os.remove('instance_model_' + cluster_name + '.yaml')
