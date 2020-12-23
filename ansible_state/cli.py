

"""
Usage:
    ansible-state [options] monitor <current-state.yml> <rules.yml>
    ansible-state [options] update-desired-state <new-state.yml>
    ansible-state [options] update-system-state <new-state.yml>
    ansible-state [options] validate <state.yml> <schema.yml>

Options:
    -h, --help              Show this page
    --debug                 Show debug logging
    --verbose               Show verbose logging
    --explain               Do not run the rules, only print the ones that would run.
    --ask-become-pass       Ask for the become password
    --project-src=<d>       Copy project files this directory [default: .]
    --inventory=<i>         Inventory to use
    --cwd=<c>               Change working directory on start
    --stream=<s>            Websocket channel to stream telemetry to
"""

from gevent import monkey
monkey.patch_all()
import gevent
import logging
import sys
import os
import yaml
from docopt import docopt
from collections import defaultdict
from getpass import getpass
import gevent_fsm.conf

from .validate import get_errors
from .monitor import AnsibleStateMonitor
from .client import ZMQClientChannel
from .server import ZMQServerChannel
from .util import ConsoleTraceLog, check_state
from .messages import DesiredState, SystemState
from .stream import WebsocketChannel

FORMAT = "[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s"
logging.basicConfig(filename='ansible_state.log', level=logging.DEBUG, format=FORMAT)  # noqa
logging.debug('Logging started')
logging.debug('Loading runner')
logging.debug('Loaded runner')

FORMAT = "[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s"
logging.basicConfig(filename='ansible_fsm.log', level=logging.DEBUG, format=FORMAT)  # noqa

logger = logging.getLogger('cli')


def main(args=None):
    if args is None:
        args = sys.argv[1:]
    parsed_args = docopt(__doc__, args)
    if parsed_args['--debug']:
        logging.basicConfig(level=logging.DEBUG)
        gevent_fsm.conf.settings.instrumented = True
    elif parsed_args['--verbose']:
        gevent_fsm.conf.settings.instrumented = True
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)

    if parsed_args['--cwd']:
        os.chdir(parsed_args['--cwd'])

    if parsed_args['monitor']:
        return ansible_state_monitor(parsed_args)
    elif parsed_args['update-desired-state']:
        return ansible_state_update_desired_state(parsed_args)
    elif parsed_args['update-system-state']:
        return ansible_state_update_system_state(parsed_args)
    elif parsed_args['validate']:
        return ansible_state_validate(parsed_args)
    else:
        assert False, 'Update the docopt'


def inventory(parsed_args):

    if not parsed_args['--inventory']:
        return "[all]\nlocalhost ansible_connection=local\n"

    with open(parsed_args['--inventory']) as f:
        return f.read()


def ansible_state_monitor(parsed_args):

    secrets = defaultdict(str)

    if parsed_args['--ask-become-pass'] and not secrets['become']:
        secrets['become'] = getpass()

    threads = []

    if parsed_args['--stream']:
        stream = WebsocketChannel(parsed_args['--stream'])
        threads.append(stream.thread)
    else:
        stream = NullChannel()

    project_src = os.path.abspath(os.path.expanduser(parsed_args['--project-src']))

    with open(parsed_args['<current-state.yml>']) as f:
        current_desired_state = yaml.safe_load(f.read())

    with open(parsed_args['<rules.yml>']) as f:
        rules = yaml.safe_load(f.read())

    tracer = ConsoleTraceLog()
    worker = AnsibleStateMonitor(tracer, 0, secrets, project_src, rules, current_desired_state, inventory(parsed_args), stream)
    threads.append(worker.thread)
    server = ZMQServerChannel(worker.queue, tracer)
    threads.append(server.zmq_thread)
    threads.append(server.controller_thread)
    worker.controller.outboxes['output'] = server.queue
    gevent.joinall(threads)
    return 0


def ansible_state_update_desired_state(parsed_args):

    with open(parsed_args['<new-state.yml>']) as f:
        new_state = f.read()
        check_state(new_state)

    client = ZMQClientChannel()
    client.send(DesiredState(0, 0, new_state))
    return 0


def ansible_state_update_system_state(parsed_args):

    with open(parsed_args['<new-state.yml>']) as f:
        new_state = f.read()
        check_state(new_state)

    client = ZMQClientChannel()
    client.send(SystemState(0, 0, new_state))
    return 0


def ansible_state_validate(parsed_args):

    with open(parsed_args['<state.yml>']) as f:
        state = yaml.safe_load(f.read())

    with open(parsed_args['<schema.yml>']) as f:
        schema = yaml.safe_load(f.read())


    for error in get_errors(state, schema):
        print(error)
    else:
        return 0
    return 1

