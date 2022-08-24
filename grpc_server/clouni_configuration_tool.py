import yaml
from yaml import Loader

from configuration_tool.common.translator_to_configuration_dsl import translate
from grpc_server.api_pb2 import ClouniConfigurationToolResponse, ClouniConfigurationToolRequest
import grpc_server.api_pb2_grpc as api_pb2_grpc
from concurrent import futures
import logging
import grpc
import argparse
import sys
import atexit
import os
import signal
from time import sleep
from functools import partial

SEPARATOR = ':'

def exit_gracefully(server, logger, x, y):
    server.stop(None)
    logger.info("Server stopped")
    print("Server stopped")
    sys.exit(0)


class TranslatorServer(object):
    def __init__(self, argv):
        self.provider_template = argv['provider_template']
        self.cluster_name = argv['cluster_name']
        self.is_delete = argv['delete']
        self.configuration_tool = argv['configuration_tool']
        self.extra = argv['extra']
        self.log_level = argv['log_level']
        self.debug = False
        if self.log_level == 'debug':
            self.debug = True
        if argv['debug']:
            self.debug = True
            self.log_level = 'debug'

        if self.extra:
            self.extra = yaml.load(self.extra, Loader=Loader)

        self.working_dir = os.getcwd()
        self.output = translate(self.provider_template, self.configuration_tool,
                                self.cluster_name, is_delete=self.is_delete, extra=self.extra,
                                    log_level=self.log_level, debug=self.debug)


class ClouniConfigurationToolServicer(api_pb2_grpc.ClouniConfigurationToolServicer):
    def __init__(self, logger):
        super().__init__()
        self.logger = logger

    def ClouniConfigurationTool(self, request, context):
        self.logger.info("Request received")
        self.logger.debug("Request content: %s", str(request))
        args = self._RequestParse(request)
        response = ClouniConfigurationToolResponse()
        try:
            self.logger.info("Request - status OK")
            response.status = ClouniConfigurationToolResponse.Status.OK
            response.content = TranslatorServer(args).output

            self.logger.info("Response send")
            return response
        except Exception as err:
            self.logger.exception("\n")
            self.logger.info("Request - status ERROR")
            response.status = ClouniConfigurationToolResponse.Status.ERROR
            response.error = str(err)
            self.logger.info("Response send")
            return response


    def _RequestParse(self, request):
        args = {}
        if request.provider_template == "":
            raise Exception("Request field 'provider_template' is required")
        else:
            args["provider_template"] = request.provider_template
        if request.cluster_name == "":
            raise Exception("Request field 'cluster_name' is required")
        else:
            args["cluster_name"] = request.cluster_name
        if request.delete:
            args['delete'] = True
        else:
            args['delete'] = False
        if request.configuration_tool != "":
            args['configuration_tool'] = request.configuration_tool
        else:
            args['configuration_tool'] = 'ansible'
        if request.extra != "":
            args['extra'] = request.extra
        else:
            args['extra'] = None
        if request.log_level != "":
            args['log_level'] = request.log_level
        else:
            args['log_level'] = 'info'
        if request.debug:
            args['debug'] = True
        else:
            args['debug'] = False
        return args


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="clouni-configuration-tool")
    parser.add_argument('--max-workers',
                        metavar='<number of workers>',
                        default=10,
                        type=int,
                        help='Maximum of working gRPC threads, default 10')
    parser.add_argument('--host',
                        metavar='<host_name/host_address>',
                        action='append',
                        help='Hosts on which server will be started, may be more than one, default [::]')
    parser.add_argument('--port', '-p',
                        metavar='<port>',
                        default=50052,
                        type=int,
                        help='Port on which server will be started, default 50052')
    parser.add_argument('--verbose', '-v',
                        action='count',
                        default=3,
                        help='Logger verbosity, default -vvv')
    parser.add_argument('--no-host-error',
                        action='store_true',
                        default=False,
                        help='If unable to start server on host:port and this option used, warning will be logged instead of critical error')
    parser.add_argument('--stop',
                        action='store_true',
                        default=False,
                        help='Stops all working servers and exit')
    parser.add_argument('--foreground',
                        action='store_true',
                        default=False,
                        help='Makes server work in foreground')
    try:
        args, args_list = parser.parse_known_args(argv)
    except argparse.ArgumentError:
        logging.critical("Failed to parse arguments. Exiting")
        raise Exception("Failed to parse arguments. Exiting")
    return args.max_workers, args.host, args.port, args.verbose, args.no_host_error, args.stop, args.foreground

def serve(argv =  None):
    # Log init
    logger = logging.getLogger("ClouniConfigurationTool server")
    fh = logging.FileHandler(".clouni-configuration-tool.log")
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    atexit.register(lambda logger: logger.info("Exited"), logger)
    # Argparse
    if argv is None:
        argv = sys.argv[1:]
    max_workers, hosts, port, verbose, no_host_error, stop, foreground = parse_args(argv)
    if stop:
        try:
            with open("/tmp/.clouni-configuration-tool.pid", mode='r') as f:
                for line in f:
                    try:
                        os.kill(int(line), signal.SIGTERM)
                    except ProcessLookupError as e:
                        print(e)
            os.remove("/tmp/.clouni-configuration-tool.pid")
        except FileNotFoundError:
            print("Working servers not found: no .clouni-configuration-tool.pid file in this directory")
        sys.exit(0)
    if not foreground:
        if os.fork():
            sleep(1)
            os._exit(0)

    # Verbosity choose
    if verbose == 1:
        logger.info("Logger level set to ERROR")
        logger.setLevel(logging.ERROR)
    elif verbose == 2:
        logger.info("Logger level set to WARNING")
        logger.setLevel(logging.WARNING)
    elif verbose == 3:
        logger.info("Logger level set to INFO")
        logger.setLevel(logging.INFO)
    else:
        logger.info("Logger level set to DEBUG")
        logger.setLevel(logging.DEBUG)

    if hosts is None:
        hosts = ['[::]', ]
    logger.info("Logging clouni-configuration-tool started")
    logger.debug("Arguments successfully parsed: max_workers %s, port %s, host %s", max_workers, port, str(hosts))
    # Argument checkа
    if max_workers < 1:
        logger.critical("Invalid max_workers argument: should be greater than 0. Exiting")
        raise Exception("Invalid max_workers argument: should be greater than 0. Exiting")
    if port == 0:
        logger.warning("Port 0 given - port will be runtime chosen - may be an error")
    if port < 0:
        logger.critical("Invalid port argument: should be greater or equal than 0. Exiting")
        raise Exception("Invalid port argument: should be greater or equal than 0. Exiting")
    # Starting server
    try:
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
        api_pb2_grpc.add_ClouniConfigurationToolServicer_to_server(
            ClouniConfigurationToolServicer(logger), server)
        host_exist = False
        for host in hosts:
            try:
                port = server.add_insecure_port(host+":"+str(port))
                host_exist = True
                logger.info("Server is going to start on %s:%s", host, port)
            except:
                if no_host_error:
                    logger.warning("Failed to start server on %s:%s", host, port)
                else:
                    logger.error("Failed to start server on %s:%s", host, port)
                    raise Exception("Failed to start server on %s:%s", host, port)
            if host_exist:
                with open("/tmp/.clouni-configuration-tool.pid", mode='a') as f:
                    f.write(str(os.getpid()) + '\n')
                server.start()
                logger.info("Server started")
                print("Server started")
            else:
                logger.critical("No host exists")
                raise Exception("No host exists")
    except Exception:
        logger.critical("Unable to start the server")
        raise Exception("Unable to start the server")
    signal.signal(signal.SIGINT, partial(exit_gracefully, server, logger))
    signal.signal(signal.SIGTERM, partial(exit_gracefully, server, logger))
    while True:
        sleep(100)

if __name__ == '__main__':
    serve()
