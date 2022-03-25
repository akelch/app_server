#!/usr/bin/env python
"""
    GOOGLE TASKS EMULATOR

    Code was taken from:
        https://gitlab.com/potato-oss/google-cloud/gcloud-tasks-emulator

    If you're interested in commercial support, training, or consultancy
    then go ahead and contact the creators at opensource@potatolondon.com
"""
import argparse
import logging
import sys

import yaml

from .server import create_server, DEFAULT_TARGET_PORT, DEFAULT_TARGET_HOST

# Random, apparently not often used
DEFAULT_PORT = 9022


def run_server(host, port, target_host, target_port, default_queue_names, max_retries,crons):
    server = create_server(host, port, target_host, target_port, default_queue_names, max_retries, crons)
    return server.run()


def read_queue_yaml(path):
    queues = []
    with open(path, "r") as f:
        try:
            data = yaml.safe_load(f)
            for queue in data['queue']:
                queues.append(queue['name'])

        except yaml.YAMLError:
            logging.exception("Error reading %s", path)
            return False, queues

    return True, queues

def read_cron_yaml(path):
    crons = []
    with open(path, "r") as f:
        try:
            data = yaml.safe_load(f)
            for cron in data['cron']:
                crons.append(cron)

        except yaml.YAMLError:
            logging.exception("Error reading %s", path)
            return False, crons

    return True, crons



def prepare_args_parser():
    parser = argparse.ArgumentParser(description='Google Cloud Task Emulator')
    subparsers = parser.add_subparsers(title='subcommands', dest="subcommand")
    start = subparsers.add_parser('start', help='start the emulator')
    start.add_argument(
        "-p", "--port",
        type=int, help='the port to run the server on', default=DEFAULT_PORT
    )
    start.add_argument(
        "-t", "--target-port",
        type=int, help='the port to which the task runner will POST requests to',
        default=DEFAULT_TARGET_PORT,
    )
    start.add_argument(
        "-H", "--target-host",
        type=str, help="the hostname to submit tasks too (default is 'localhost')",
        default=DEFAULT_TARGET_HOST,
    )
    start.add_argument("-q", "--quiet", action="store_true", default=False)
    start.add_argument(
        "-d", "--default-queue", type=str, action="append",
        help="If specified will create a queue with the passed name. "
             "Name should be in the format of projects/PROJECT_ID/locations/LOCATION_ID/queues/QUEUE_ID"
    )
    start.add_argument(
        "-r", "--max-retries",
        type=int, help='maximum number of retries when a task is failed (default is infinity)',
        default=-1,
    )
    start.add_argument(
        "-Q", "--queue-yaml",
        type=str, help="path to a queue.yaml to initialize the default queues",
        default=None
    )

    start.add_argument(
        "-C", "--cron-yaml",
        type=str, help="path to a cron.yaml to initialize the default queues",
        default=None
    )

    start.add_argument(
        "-P", "--queue-yaml-project",
        type=str, help="project ID to use for queues created from queue-yaml",
        default="[PROJECT]"
    )
    start.add_argument(
        "-L", "--queue-yaml-location",
        type=str, help="location ID to use for queues created from queue-yaml",
        default="[LOCATION]"
    )
    return parser


def main():
    print("Starting Cloud Tasks Emulator")
    parser = prepare_args_parser()
    args = parser.parse_args()
    if args.subcommand != "start":
        parser.print_usage()
        sys.exit(1)
    root = logging.getLogger()
    root.addHandler(logging.StreamHandler())
    if args.quiet:
        root.setLevel(logging.ERROR)
    else:
        root.setLevel(logging.INFO)
    default_queues = set(args.default_queue or [])
    crons = []
    # FIXME: We simply read queue names from queue.yaml. Instead we should
    # read all queue parameters and handle them correctly in the emulator
    if args.queue_yaml:
        success, queues = read_queue_yaml(args.queue_yaml)
        queues = [
            "projects/%s/locations/%s/queues/%s" % (
                args.queue_yaml_project,
                args.queue_yaml_location,
                x
            ) for x in queues
        ]

        if success:
            default_queues = default_queues.union(set(queues))
        else:
            sys.exit(1)

    if args.cron_yaml:
        success, crons =read_cron_yaml(args.cron_yaml)

    sys.exit(run_server("localhost", args.port, args.target_host, args.target_port, default_queues, args.max_retries,crons))


if __name__ == '__main__':
    main()
