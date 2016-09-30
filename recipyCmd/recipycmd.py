#!/usr/bin/env python
import os
import sys
import re
import six
import click
from colorama import init as colorama_init
from json import dumps
from tinydb import where, Query

from recipyCommon import config, utils
from recipyCommon.config import read_config_file
from recipyCommon.utils import get_run
from recipyCommon.version_control import hash_file
from recipyCmd.templating import render_run_template, render_debug_template

colorama_init()


class CliConfig(object):
    """Passes configuration between commands."""
    def __init__(self):
        self.debug = False
        self.db = utils.open_or_create_db()

pass_config = click.make_pass_decorator(CliConfig, ensure=True)


class CLI(click.MultiCommand):
    """Implements MultiCommand click class methods to find other
    commands in the cmd_folder. Command files must be in
    format cmd_COMMANDNAME.py"""

    def list_commands(self, ctx):
        cmd = []
        cmd_folder = os.path.dirname(__file__)
        for filename in os.listdir(cmd_folder):
            if filename.endswith('.py') and filename.startswith('cmd_'):
                cmd.append(filename[4:-3])
        cmd.sort()
        return cmd

    def get_command(self, ctx, cmd_name):
        try:
            if sys.version_info[0] == 2:
                cmd_name = cmd_name.encode('ascii', 'replace')
            mod = __import__('recipyCmd.cmd_' + cmd_name, fromlist=['cmd'])
        except ImportError:
            return
        return mod.cmd


@click.command(cls=CLI)
@click.version_option(version='0.1.0')
@click.option('--debug', is_flag=True,
              help='Show debug info while running command.')
@pass_config
def main(config, debug):
    """Frictionless provenance tracking in Python.
    For more info type: recipy COMMAND --help"""
    config.debug = debug
    if config.debug:
        click.echo(debug_info())


def debug_info():
    cnf = read_config_file()
    s = six.StringIO()
    cnf.write(s)
    return render_debug_template(config.get_db_path(), s.getvalue())


def gui(args):
    """
    Loads recipy GUI from the command-line
    """
    from recipyGui import recipyGui
    import threading
    import webbrowser
    import socket

    def get_free_port():
        port = None
        base_port = config.get_gui_port()
        for trial_port in range(base_port, base_port + 5):
            try:
                s = socket.socket()
                s.bind(('', trial_port))
                s.close()
                port = trial_port
                break
            except Exception:
                # port already bound
                # Please note that this also happens when the gui is run in
                # debug mode!
                pass
        if not port:
            # no free ports above, fall back to random
            s = socket.socket()
            s.bind(('', 0))
            port = s.getsockname()[1]
            s.close()
        return port

    port = get_free_port()
    url = "http://127.0.0.1:{0}".format(port)

    if not args['--no-browser']:
        # Give the application some time before it starts
        threading.Timer(1.25, lambda: webbrowser.open(url)).start()

    # Turn off reloading by setting debug = False (this also fixes starting the
    # application twice)
    recipyGui.run(debug=args['--debug'], port=port)


def latest(args):
    run = get_run(db, latest=True)

    if not run:
        if args['--json']:
            print('[]')
            return
        else:
            print("Database is empty")
            return

    if args['--json']:
        output = dumps(run, indent=2, sort_keys=True, default=utils.json_serializer)
        print(output)
    else:
        print(render_run_template(run))

        if args['--diff']:
            if 'diff' in run:
                print("\n\n")
                print(run['diff'])


def find_by_hash(x, val):
    for output in x:
        if isinstance(output, six.string_types):
            # If it's just a string it doesn't have a hash
            # so skip it
            return False
        else:
            test_val = output[1]

        if test_val == val:
            return True


def find_by_filepath(x, val):
    for output in x:
        if isinstance(output, six.string_types):
            test_val = output
        else:
            test_val = output[0]
        if test_val == val:
            return True


def find_by_regex(x, val):
    for output in x:
        if isinstance(output, six.string_types):
            test_val = output
        else:
            test_val = output[0]
        if re.match(val, test_val):
            return True


def search_hash(args):
    try:
        hash_value = hash_file(args['<outputfile>'])
    except Exception:
        # Probably an invalid filename/path so assume it is a raw hash value instead
        hash_value = args['<outputfile>']

    Run = Query()
    # Search both outputs AND inputs
    # TODO: Add a command-line argument to force searching of just one
    # of inputs or outputs
    results = db.search(Run.outputs.test(find_by_hash, hash_value))
    results += db.search(Run.inputs.test(find_by_hash, hash_value))

    results = sorted(results, key=lambda x: x['date'])

    if args['--json']:
        if len(results) == 0:
            print('[]')
            return
        if args['--all']:
            res_to_output = results
        else:
            res_to_output = results[-1]
        output = dumps(res_to_output, indent=2, sort_keys=True, default=utils.json_serializer)
        print(output)
    else:
        if len(results) == 0:
            print('No results found')
        else:
            if args['--all']:
                for r in results[:-1]:
                    print(render_run_template(r))
                    print("-" * 40)
                print(render_run_template(results[-1]))
            else:
                print(render_run_template(results[-1]))
                if len(results) > 1:
                    print("** Previous runs have been "
                          "found. Run with --all to show. **")

                if args['--diff']:
                    if 'diff' in results[-1]:
                        print("\n\n")
                        print(results[-1]['diff'])

    db.close()


def search(args):
    if args['--fuzzy'] or args['--id'] or args['--regex'] or args['--filepath']:
        search_text(args)
    else:
        search_hash(args)


def search_text(args):
    filename = args['<outputfile>']

    Run = Query()

    if args['--fuzzy']:
        results = db.search(Run.outputs.test(find_by_regex, ".+%s.+" % filename))
        results += db.search(Run.inputs.test(find_by_regex, ".+%s.+" % filename))
    elif args['--regex']:
        results = db.search(Run.outputs.test(find_by_regex, filename))
        results += db.search(Run.inputs.test(find_by_regex, filename))
    elif args['--id']:
        results = db.search(where('unique_id').matches('%s.*' % filename))
        # Automatically turn on display of all results so we don't misleadingly
        # suggest that their shortened ID is unique when it isn't
        args['--all'] = True
    elif args['--filepath']:
        results = db.search(Run.outputs.test(find_by_filepath, os.path.abspath(filename)))
        results += db.search(Run.inputs.test(find_by_filepath, os.path.abspath(filename)))
    else:
        print('Unknown arguments')
        print(__doc__)
        return

    # Sort the results
    results = sorted(results, key=lambda x: x['date'])

    if args['--json']:
        if len(results) == 0:
            print('[]')
            return
        if args['--all']:
            res_to_output = results
        else:
            res_to_output = results[-1]
        output = dumps(res_to_output, indent=2, sort_keys=True, default=utils.json_serializer)
        print(output)
    else:
        if len(results) == 0:
            print("No results found")
        else:
            if args['--all']:
                for r in results[:-1]:
                    print(render_run_template(r))
                    print("-" * 40)
                print(render_run_template(results[-1]))
            else:
                print(render_run_template(results[-1]))
                if len(results) > 1:
                    print("** Previous runs have been "
                          "found. Run with --all to show. **")

                if args['--diff']:
                    if 'diff' in results[-1]:
                        print("\n\n")
                        print(results[-1]['diff'])

    db.close()