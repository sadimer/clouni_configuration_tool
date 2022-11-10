import unittest

from testing.base import BaseAnsibleProvider
from configuration_tool.common.tosca_reserved_keys import TOSCA_DEFINITIONS_VERSION, TOPOLOGY_TEMPLATE, NODE_TEMPLATES, \
    TYPE, CAPABILITIES, PROPERTIES



class TestKubernetesOutput(unittest.TestCase, BaseAnsibleProvider):
    PROVIDER = 'kubernetes'
