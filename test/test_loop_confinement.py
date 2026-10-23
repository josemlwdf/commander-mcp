import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import support
from lib.subagents import continue_config as cc
from lib.subagents import loop
from lib.subagents.roles import get_role


class LoopConfinementTests(unittest.TestCase):
    """Loop tool calls bypass before_tool_call; path confinement comes from inside()."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.ws = base / 'ws'
        self.ws.mkdir()
        self.outside = base / 'outside.txt'
        old_root, old_wd = support.ROOT, support.WORKING_DIRECTORY
        self.addCleanup(setattr, support, 'ROOT', old_root)
        self.addCleanup(setattr, support, 'WORKING_DIRECTORY', old_wd)
        support.configure(str(self.ws))

    def run_with(self, role_name, first_action):
        role = get_role(role_name)
        self.assertIsNotNone(role)
        seen = []

        def sampling_fn(_system, messages, _max):
            seen.append(messages[-1]['content'])
            if len(seen) == 1:
                return json.dumps(first_action)
            return json.dumps({'final': '- done'})

        loop.run_loop('sampling', role, 'task', None, None,
                      sampling_fn=sampling_fn, allow_write=True)
        return seen[-1]

    def test_create_file_cannot_escape_workspace(self):
        for target in ('../outside.txt', str(self.outside), 'a/../../outside.txt'):
            with self.subTest(target=target):
                feedback = self.run_with('coder', {
                    'tool': 'create_file',
                    'args': {'path': target, 'content': 'pwned'},
                })
                self.assertFalse(self.outside.exists())
                self.assertIn('result JSON', feedback)
                self.assertIn('error', feedback.lower())

    def test_apply_edits_cannot_touch_outside_file(self):
        self.outside.write_text('original')
        feedback = self.run_with('coder', {
            'tool': 'apply_edits',
            'args': {'edits': [{'path': '../outside.txt', 'pattern': 'original',
                                'replacement': 'changed'}]},
        })
        self.assertEqual(self.outside.read_text(), 'original')
        self.assertIn('error', feedback.lower())

    def test_read_files_cannot_read_outside_file(self):
        self.outside.write_text('TOP-SECRET-CONTENT')
        feedback = self.run_with('explorer', {
            'tool': 'read_files',
            'args': {'paths': ['../outside.txt']},
        })
        self.assertNotIn('TOP-SECRET-CONTENT', feedback)

    def test_write_role_refused_without_gate(self):
        role = get_role('coder')
        with patch.dict('os.environ', {}, clear=False):
            import os
            os.environ.pop('SUBAGENT_LOOP_WRITE', None)
            with self.assertRaises(RuntimeError) as ctx:
                loop.run_loop('sampling', role, 't', None, None,
                              sampling_fn=lambda *a: '{"final":"x"}')
        self.assertIn('SUBAGENT_LOOP_WRITE', str(ctx.exception))


class ToolDeadlineTests(unittest.TestCase):
    def test_timeout_returns_error_but_thread_keeps_running(self):
        # Documents a known limitation: the deadline abandons the thread, it does
        # not stop it, so a slow write can still land after the timeout.
        finished = threading.Event()

        def slow(**_kwargs):
            time.sleep(0.3)
            finished.set()
            return {'ok': True}

        result = loop._call_tool(slow, {}, deadline=0.05)
        self.assertIn('timed out', result['error'])
        self.assertFalse(finished.is_set())
        self.assertTrue(finished.wait(2))


class NoPyYamlTests(unittest.TestCase):
    def test_yaml_config_skipped_when_pyyaml_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'c.yaml'
            path.write_text('models: []', encoding='utf-8')
            with patch.dict(sys.modules, {'yaml': None}):  # import yaml -> ImportError
                self.assertIsNone(cc.load_config_file(path))

    def test_json_config_still_works_when_pyyaml_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'c.json'
            path.write_text('{"models": []}', encoding='utf-8')
            with patch.dict(sys.modules, {'yaml': None}):
                self.assertEqual(cc.load_config_file(path), {'models': []})


if __name__ == '__main__':
    unittest.main()