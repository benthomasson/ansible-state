
import os
import yaml
import tempfile
import shutil
import json
import ansible_runner
from pprint import pprint
from collections import OrderedDict
from deepdiff import DeepDiff, extract

from ansible_state.rule import select_rules_recursive, Action, ACTION_RULES
from ansible_state.util import ensure_directory


def convert_diff(diff):

    print(diff)
    if 'dictionary_item_added' in diff:
        diff['dictionary_item_added'] = [str(x) for x in diff['dictionary_item_added']]
    if 'dictionary_item_removed' in diff:
        diff['dictionary_item_removed'] = [str(x) for x in diff['dictionary_item_removed']]
    if 'type_changes' in diff:
        diff['type_changes'] = [str(x) for x in diff['type_changes']]
    diff = dict(diff)
    print(yaml.safe_dump(diff))
    return diff


class PlaybookRunner:

    def __init__(self, new_desired_state, state_diff, destructured_vars, playbook, secrets, project_src, inventory):
        print('PlaybookRunner')
        self.inventory = inventory
        self.secrets = secrets
        self.project_src = project_src
        self.new_desired_state = new_desired_state
        self.state_diff = convert_diff(state_diff)
        self.destructured_vars = destructured_vars
        self.playbook = playbook
        self.runner_thread = None
        self.shutdown_requested = False
        self.shutdown = False

        self.build_project_directory()
        self.copy_files()
        self.write_settings()
        self.write_cmdline()
        self.write_passwords()
        self.write_state_vars()
        self.write_diff_vars()
        self.write_destructred_vars()
        self.write_playbook()
        self.write_inventory()
        self.start_ansible_playbook()

    def build_project_directory(self):
        self.temp_dir = tempfile.mkdtemp(prefix="ansible_state_playbook")
        print(self.temp_dir)
        ensure_directory(os.path.join(self.temp_dir, 'env'))
        ensure_directory(os.path.join(self.temp_dir, 'project'))
        ensure_directory(os.path.join(self.temp_dir, 'project', 'roles'))

    def copy_files(self):
        src = os.path.abspath(self.project_src)
        dest = os.path.join(self.temp_dir, 'project')
        src_files = os.listdir(src)
        for file_name in src_files:
            full_file_name = os.path.join(src, file_name)
            if (os.path.isfile(full_file_name)):
                shutil.copy(full_file_name, dest)
            if (os.path.isdir(full_file_name)):
                shutil.copytree(full_file_name, os.path.join(dest, file_name))

    def write_settings(self):
        with open(os.path.join(self.temp_dir, 'env', 'settings'), 'w') as f:
            f.write(json.dumps(dict(idle_timeout=0,
                                    job_timeout=0)))

    def write_cmdline(self):
        with open(os.path.join(self.temp_dir, 'env', 'cmdline'), 'w') as f:
            f.write("--ask-become-pass -v")

    def write_passwords(self):
        with open(os.path.join(self.temp_dir, 'env', 'passwords'), 'w') as f:
            f.write("""---\n"SUDO password:": "{0}"\nBECOME password: "{0}"\n...""".format(self.secrets['become']))

    def write_playbook(self):
        self.playbook_file = (os.path.join(self.temp_dir, 'project', 'playbook.yml'))
        playbook = self.playbook
        with open(self.playbook_file, 'w') as f:
            f.write(yaml.safe_dump(playbook, default_flow_style=False))

    def write_state_vars(self):
        state_vars_file = os.path.join(self.temp_dir, 'project', 'state_vars.yml')
        with open(state_vars_file, 'w') as f:
            f.write(yaml.safe_dump(self.new_desired_state, default_flow_style=False))
        for play in self.playbook:
            play['tasks'].insert(0, {'include_vars': {'file': 'state_vars.yml', 'name': 'state'}})

    def write_diff_vars(self):
        diff_vars_file = os.path.join(self.temp_dir, 'project', 'diff_vars.yml')
        with open(diff_vars_file, 'w') as f:
            f.write(yaml.safe_dump(self.state_diff, default_flow_style=False))
        for play in self.playbook:
            play['tasks'].insert(0, {'include_vars': {'file': 'diff_vars.yml', 'name': 'diff'}})

    def write_destructred_vars(self):
        diff_vars_file = os.path.join(self.temp_dir, 'project', 'destructured_vars.yml')
        with open(diff_vars_file, 'w') as f:
            f.write(yaml.safe_dump(self.destructured_vars, default_flow_style=False))
        for play in self.playbook:
            play['tasks'].insert(0, {'include_vars': {'file': 'destructured_vars.yml'}})

    def write_inventory(self):
        print("inventory set to %s", self.inventory)
        with open(os.path.join(self.temp_dir, 'inventory'), 'w') as f:
            f.write(self.inventory)

    def start_ansible_playbook(self):
        print('start_ansible_playbook')
        ansible_runner.run(private_data_dir=self.temp_dir,
                           playbook="playbook.yml",
                           quiet=True,
                           debug=True,
                           ignore_logging=True,
                           cancel_callback=self.cancel_callback,
                           finished_callback=self.finished_callback,
                           event_handler=self.runner_process_message)
        print('spawned ansible runner')
        print(self.temp_dir)

    def cancel_callback(self):
        print('cancel_callback called')
        return self.shutdown_requested

    def finished_callback(self, runner):
        print('finished_callback called')
        self.shutdown = True

    def runner_process_message(self, data):
        # print("runner message:\n{}".format(pprint.pformat(data)))
        print(data.get('stdout', ''))


def ansible_state_diff(secrets, project_src, current_desired_state, new_desired_state, rules, inventory, explain):

    # Find matching rules

    diff = DeepDiff(current_desired_state, new_desired_state)
    print(diff)

    matching_rules = select_rules_recursive(diff, rules['rules'])
    if explain:
        print('matching_rules')
        pprint(matching_rules)

    dedup_matching_rules = OrderedDict()

    for matching_rule in matching_rules:
        _, _, match, _ = matching_rule
        changed_subtree_path = match.groups()[0]
        if changed_subtree_path not in dedup_matching_rules:
            dedup_matching_rules[changed_subtree_path] = matching_rule

    dedup_matching_rules = list(dedup_matching_rules.values())

    if explain:
        print('dedup_matching_rules:')
        pprint(dedup_matching_rules)

    for change_type, rule, match, value in dedup_matching_rules:
        print('change_type', change_type)
        print('rule', rule)
        print('match', match)
        print('value', value)
        changed_subtree_path = match.groups()[0]
        print('changed_subtree_path', changed_subtree_path)
        try:
            new_subtree = extract(new_desired_state, changed_subtree_path)
            new_subtree_missing = False
        except (KeyError, IndexError, TypeError):
            new_subtree_missing = True
        try:
            old_subtree = extract(current_desired_state, changed_subtree_path)
            old_subtree_missing = False
        except (KeyError, IndexError, TypeError):
            old_subtree_missing = True
        print('new_subtree_missing', new_subtree_missing)
        print('old_subtree_missing', old_subtree_missing)

        if new_subtree_missing is False and old_subtree_missing is False:
            action = Action.UPDATE
            subtree = new_subtree
        elif new_subtree_missing and old_subtree_missing is False:
            action = Action.DELETE
            subtree = old_subtree
        elif old_subtree_missing and new_subtree_missing is False:
            action = Action.CREATE
            subtree = new_subtree
        else:
            assert False, "Logic bug"
        print('action', action)

        print('rule action', rule.get(ACTION_RULES[action]))

        # Experiment: Build the vars using destructuring

        destructured_vars = {}

        for name, extract_path in rule.get('vars', {}).items():
            destructured_vars[name] = extract(subtree, extract_path)

        # Experiment: Make the subtree available as node
        destructured_vars['node'] = subtree

        print('destructured_vars', destructured_vars)

        # Determine the inventory to run on

        inventory_selector = rule.get('inventory_selector')
        if inventory_selector:
            try:
                inventory_name = extract(subtree, inventory_selector)
            except KeyError:
                raise Exception(f'Invalid inventory_selector {inventory_selector}')

        print('inventory_name', inventory_name)

        # Build a playbook using tasks or role from rule

        playbook = [{'name': 'generated playbook',
                     'hosts': inventory_name,
                     'gather_facts': False,
                     'tasks': []}]

        if 'tasks' in rule.get(ACTION_RULES[action], {}):
            playbook[0]['tasks'].append({'include_tasks': {'file': rule.get(ACTION_RULES[action]).get('tasks')}})

        if 'become' in rule:
            playbook[0]['become'] = rule['become']

        if explain:
            print(yaml.dump(playbook))

        else:

            # Run the playbook

            PlaybookRunner(new_desired_state,
                           diff,
                           destructured_vars,
                           playbook,
                           secrets,
                           project_src,
                           inventory)

    return 0