import setuptools

reqs = ['six', 'grpcio==1.37.1', 'grpcio-tools==1.37.1', 'pyyaml', 'graphlib_backport', 'clouni-tosca-parser']
setuptools.setup(install_requires=reqs)