"""Isolation tests for the subagent loop, using a mocked provider (no network).

The "parent" canary exists only in this test's own scope, standing in for the
orchestrator's private conversation. It must never appear in any request the
loop sends to the provider.
"""
import dataclasses
import json
import unittest
from unittest.mock import patch

from lib.subagents import loop
from lib.subagents.continue_config import ContinueModel
from lib.subagents.roles import get_role

PARENT_CANARY = 'ORCH-CANARY-7f3a91'
FAKE_KEY = 'sk-isolation-test-0000'


def reply(text):
    return {'choices': [{'message': {'content': text}}]}


def tool_action(name, **args):
    return json.dumps({'tool': name, 'args': args})


def final_action(text):
    return json.dumps({'final': text})


class FakeProvider:
    """Scripted stand-in for loop._post_json; records every payload sent."""

    def __init__(self, script):
        self.script = list(script)
        self.payloads = []
        self.urls = []

    def __call__(self, url, payload, headers, timeout=35.0, secrets=()):
        self.urls.append(url)
        # Deep copy via JSON: the loop mutates its history list between turns.
        self.payloads.append(json.loads(json.dumps(payload)))
        if not self.script:
            raise AssertionError('provider called more times than scripted')
        return reply(self.script.pop(0))


MODEL = ContinueModel(
    name='mock', provider='openai', model='mock-model', api_base=None, api_key=FAKE_KEY
)


def run(role, script, task='find the answer', context=None, deliverable=None, tools=None):
    provider = FakeProvider(script)
    with patch.object(loop, '_post_json', provider):
        if tools is not None:
            with patch.dict(loop._ALLOWED_TOOLS, tools, clear=True):
                out = loop.run_loop('continue:openai', role, task, context, deliverable,
                                    continue_model=MODEL)
        else:
            out = loop.run_loop('continue:openai', role, task, context, deliverable,
                                continue_model=MODEL)
    return out, provider


class HistoryIsolationTests(unittest.TestCase):
    def test_first_request_has_only_system_prompt_and_task(self):
        role = get_role('explorer')
        _, provider = run(role, [final_action('- done')], task='T-ONE',
                          context='C-ONE', deliverable='D-ONE')
        messages = provider.payloads[0]['messages']
        self.assertEqual([m['role'] for m in messages], ['system', 'user'])
        self.assertIn(role.system_prompt, messages[0]['content'])
        self.assertIn('Allowed tools: ' + ', '.join(role.allowed_tools), messages[0]['content'])
        for part in ('Task: T-ONE', 'Context: C-ONE', 'Deliverable: D-ONE'):
            self.assertIn(part, messages[1]['content'])

    def test_parent_canary_never_reaches_provider(self):
        # The canary lives only in the parent scope; the task args do not carry it.
        parent_history = [{'role': 'user', 'content': 'secret ' + PARENT_CANARY}]
        self.assertIn(PARENT_CANARY, json.dumps(parent_history))
        tools = {'read_files': lambda **kw: {'files': []}}
        _, provider = run(get_role('explorer'),
                          [tool_action('read_files', paths=['x']), final_action('- ok')],
                          tools=tools)
        for payload in provider.payloads:
            self.assertNotIn(PARENT_CANARY, json.dumps(payload))

    def test_later_turns_contain_only_this_loops_history(self):
        tools = {'read_files': lambda **kw: {'files': ['r']}}
        _, provider = run(get_role('explorer'),
                          [tool_action('read_files', paths=['x']), final_action('- ok')],
                          task='T-TWO', tools=tools)
        second = provider.payloads[1]['messages']
        self.assertEqual([m['role'] for m in second], ['system', 'user', 'assistant', 'user'])
        self.assertIn('Task: T-TWO', second[1]['content'])
        self.assertIn("Tool 'read_files' result JSON", second[3]['content'])

    def test_consecutive_runs_do_not_share_history(self):
        _, first = run(get_role('explorer'), [final_action('- a')], task='ALPHA-TOKEN')
        _, second = run(get_role('explorer'), [final_action('- b')], task='BETA')
        self.assertIn('ALPHA-TOKEN', json.dumps(first.payloads))
        self.assertNotIn('ALPHA-TOKEN', json.dumps(second.payloads))
        self.assertEqual(len(second.payloads[0]['messages']), 2)

    def test_api_key_only_in_auth_header_not_in_payload(self):
        _, provider = run(get_role('explorer'), [final_action('- ok')])
        self.assertNotIn(FAKE_KEY, json.dumps(provider.payloads))


class AllowlistTests(unittest.TestCase):
    def test_tool_outside_allowlist_is_rejected_and_not_executed(self):
        calls = []
        tools = {
            'read_files': lambda **kw: {'files': []},
            'apply_edits': lambda **kw: calls.append(kw) or {'ok': True},
        }
        with self.assertRaises(RuntimeError) as ctx:
            run(get_role('explorer'), [tool_action('apply_edits', edits=[])], tools=tools)
        self.assertIn('outside role allowlist', str(ctx.exception))
        self.assertEqual(calls, [])

    def test_unknown_tool_name_is_rejected(self):
        with self.assertRaises(RuntimeError) as ctx:
            run(get_role('explorer'), [tool_action('rm_rf_everything')], tools={})
        self.assertIn('outside role allowlist', str(ctx.exception))

    def test_write_role_requires_loop_write_opt_in(self):
        provider = FakeProvider([final_action('done')])
        with patch.object(loop, '_post_json', provider):
            with self.assertRaises(RuntimeError) as ctx:
                loop.run_loop('continue:openai', get_role('coder'), 't', None, None,
                              continue_model=MODEL, allow_write=False)
        self.assertIn('SUBAGENT_LOOP_WRITE=1', str(ctx.exception))
        self.assertEqual(provider.payloads, [])


class CallCapTests(unittest.TestCase):
    def test_tool_call_budget_is_enforced(self):
        calls = []
        tools = {'read_files': lambda **kw: calls.append(kw) or {'files': []}}
        role = dataclasses.replace(get_role('explorer'), max_tool_calls=2, max_turns=10)
        with self.assertRaises(RuntimeError) as ctx:
            run(role, [tool_action('read_files', paths=['x'])] * 5, tools=tools)
        self.assertIn('tool-call budget exceeded', str(ctx.exception))
        self.assertEqual(len(calls), 2)

    def test_turn_limit_is_enforced(self):
        tools = {'read_files': lambda **kw: {'files': []}}
        role = dataclasses.replace(get_role('explorer'), max_tool_calls=50, max_turns=3)
        with self.assertRaises(RuntimeError) as ctx:
            run(role, [tool_action('read_files', paths=['x'])] * 3, tools=tools)
        self.assertIn('max turns exceeded', str(ctx.exception))

    def test_final_result_returns_turns_and_tokens(self):
        result, turns, tokens = run(get_role('explorer'), [final_action('- evidence')])[0]
        self.assertEqual(result, '- evidence')
        self.assertEqual(turns, 1)
        self.assertGreaterEqual(tokens, 1)


if __name__ == '__main__':
    unittest.main()