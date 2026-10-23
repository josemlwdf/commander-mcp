import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from lib.subagents import loop
from lib.subagents.continue_config import ContinueModel
from lib.subagents.roles import get_role

KEY = 'sk-secret-1234'
HDR = 'hdr-secret-5678'
SEEN = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length))
        headers = {k.lower(): v for k, v in self.headers.items()}
        SEEN.append((self.path, headers, body))
        if self.path.startswith('/fail'):
            payload = {'error': 'bad ' + headers.get('authorization', '') + headers.get('x-extra', '')}
            status = 401
        elif self.path.endswith('/messages'):
            text = json.dumps({'final': '- finding: anthropic ok'})
            payload = {'content': [{'type': 'text', 'text': text}]}
            status = 200
        else:
            text = json.dumps({'final': '- finding: openai ok'})
            payload = {'choices': [{'message': {'content': text}}]}
            status = 200
        out = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(out)


def make_model(provider, base, key=KEY, headers=None, max_tokens=None):
    return ContinueModel(
        name='m', provider=provider, model='mod', api_base=base, api_key=key,
        roles=('chat',), headers=headers or {}, max_tokens=max_tokens,
    )


class FakeServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(('127.0.0.1', 0), Handler)
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        SEEN.clear()
        self.role = get_role('explorer')

    def run_continue(self, model, role=None, **kw):
        return loop.run_loop(
            'continue:' + model.provider, role or self.role, 'task', None, None,
            continue_model=model, **kw,
        )

    def test_ollama_base_gets_v1_cap_and_no_auth(self):
        result = self.run_continue(make_model('ollama', self.base, key=None, max_tokens=300))
        self.assertIn('openai ok', result[0])
        path, headers, body = SEEN[-1]
        self.assertEqual(path, '/v1/chat/completions')
        self.assertEqual(body['max_tokens'], 300)
        self.assertNotIn('authorization', headers)

    def test_max_tokens_is_only_an_upper_bound(self):
        self.run_continue(make_model('openai', self.base, max_tokens=999999))
        self.assertLessEqual(SEEN[-1][2]['max_tokens'], 4096)

    def test_openai_sends_bearer_key_and_extra_headers(self):
        self.run_continue(make_model('openai', self.base, headers={'X-Extra': 'e1'}))
        headers = SEEN[-1][1]
        self.assertEqual(headers['authorization'], 'Bearer ' + KEY)
        self.assertEqual(headers['x-extra'], 'e1')

    def test_config_authorization_header_is_not_duplicated(self):
        self.run_continue(make_model('openai', self.base, headers={'Authorization': 'Bearer ' + HDR}))
        self.assertEqual(SEEN[-1][1]['authorization'], 'Bearer ' + HDR)

    def test_anthropic_uses_x_api_key_and_drops_authorization(self):
        model = make_model('anthropic', self.base, headers={'Authorization': 'Bearer ' + KEY, 'X-Extra': 'e1'})
        result = self.run_continue(model)
        self.assertIn('anthropic ok', result[0])
        path, headers, body = SEEN[-1]
        self.assertEqual(path, '/v1/messages')
        self.assertEqual(headers['x-api-key'], KEY)
        self.assertNotIn('authorization', headers)
        self.assertEqual(headers['x-extra'], 'e1')
        self.assertEqual(body['model'], 'mod')

    def test_http_error_text_redacts_key_and_header_values(self):
        model = make_model('openai', self.base + '/fail', headers={'X-Extra': HDR})
        with self.assertRaises(RuntimeError) as ctx:
            self.run_continue(model)
        text = str(ctx.exception)
        self.assertIn('401', text)
        self.assertNotIn(KEY, text)
        self.assertNotIn(HDR, text)

    def test_connection_error_does_not_leak_key(self):
        model = make_model('openai', 'http://127.0.0.1:1')
        with self.assertRaises(RuntimeError) as ctx:
            self.run_continue(model)
        self.assertNotIn(KEY, str(ctx.exception))

    def test_write_role_gate(self):
        coder = get_role('coder')
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SUBAGENT_LOOP_WRITE', None)
            with self.assertRaises(RuntimeError) as ctx:
                self.run_continue(make_model('openai', self.base), role=coder)
        self.assertIn('SUBAGENT_LOOP_WRITE=1', str(ctx.exception))
        self.assertEqual(SEEN, [])
        with patch.dict(os.environ, {'SUBAGENT_LOOP_WRITE': '1'}):
            result = self.run_continue(make_model('openai', self.base), role=coder)
        self.assertIn('openai ok', result[0])
        self.assertIn('openai ok', self.run_continue(make_model('openai', self.base), role=coder, allow_write=True)[0])

    def test_committer_is_gated_too(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SUBAGENT_LOOP_WRITE', None)
            with self.assertRaises(RuntimeError):
                self.run_continue(make_model('openai', self.base), role=get_role('committer'))

    def test_cancel_before_first_completion(self):
        class Event:
            def is_set(self):
                return True

        with self.assertRaises(loop.SubagentCancelled):
            self.run_continue(make_model('openai', self.base), cancel_event=Event())
        self.assertEqual(SEEN, [])

    def test_cancel_after_completion_and_before_tool(self):
        class Event:
            def __init__(self):
                self.calls = 0

            def is_set(self):
                self.calls += 1
                return self.calls >= 2

        with self.assertRaises(loop.SubagentCancelled):
            self.run_continue(make_model('openai', self.base), cancel_event=Event())

    def test_unsupported_provider_and_missing_model(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.run_continue(make_model('gemini', self.base))
        self.assertIn('Unsupported continue provider: gemini', str(ctx.exception))
        with self.assertRaises(RuntimeError) as ctx:
            loop.run_loop('continue:openai', self.role, 't', None, None)
        self.assertIn('requires a resolved model', str(ctx.exception))


class HelperTests(unittest.TestCase):
    def test_plain_text_response_is_accepted_as_final(self):
        self.assertEqual(
            loop._parse_json_action("Here is the result in plain text."),
            {"kind": "final", "text": "Here is the result in plain text."},
        )

    def test_prompt_describes_tool_arguments_and_status_tools(self):
        from lib.subagents.roles import get_role

        role = get_role('explorer')
        seen = {}

        def sample(prompt, history, max_tokens):
            seen['prompt'] = prompt
            return '{"final":"done"}'

        loop.complete_once('sampling', role, 'task', None, None, [], sampling_fn=sample)
        self.assertIn('text or regex', seen['prompt'])
        self.assertIn('subagent_status', seen['prompt'])
        self.assertIn('pattern and replacement', seen['prompt'])

    def test_loop_status_and_result_tools_are_available(self):
        from unittest.mock import patch

        with patch('lib.subagents.tools.subagent_status', return_value={'state': 'done'}) as status:
            self.assertEqual(loop._ALLOWED_TOOLS['subagent_status'](task_id='t_1'), {'state': 'done'})
            status.assert_called_once_with(task_id='t_1')
        with patch('lib.subagents.tools.subagent_result', return_value={'result': 'ok'}) as result:
            self.assertEqual(loop._ALLOWED_TOOLS['subagent_result'](task_id='t_1'), {'result': 'ok'})
            result.assert_called_once_with(task_id='t_1')

    def test_provider_support(self):
        for name in ('openai', 'anthropic', 'ollama', 'openrouter', 'deepseek', 'mistral', 'groq', 'together', 'lmstudio'):
            self.assertTrue(loop.continue_provider_supported(name), name)
        self.assertFalse(loop.continue_provider_supported('gemini'))
        self.assertFalse(loop.continue_provider_supported('bedrock'))

    def test_base_url_rules(self):
        self.assertEqual(loop._continue_base_url('openai', None), 'https://api.openai.com/v1')
        self.assertEqual(loop._continue_base_url('ollama', 'http://h:11434/'), 'http://h:11434/v1')
        self.assertEqual(loop._continue_base_url('lmstudio', 'http://h:1234/v1'), 'http://h:1234/v1')
        self.assertEqual(loop._continue_base_url('openrouter', 'http://proxy/v1/'), 'http://proxy/v1')

    def test_anthropic_url_rules(self):
        self.assertEqual(loop._anthropic_url(None), 'https://api.anthropic.com/v1/messages')
        self.assertEqual(loop._anthropic_url('http://h/'), 'http://h/v1/messages')
        self.assertEqual(loop._anthropic_url('http://h/v1'), 'http://h/v1/messages')

    def test_redact(self):
        self.assertEqual(loop._redact('a sk-secret-1234 b', ['sk-secret-1234']), 'a [redacted] b')
        self.assertEqual(loop._redact('abc', ['abc']), 'abc')
        self.assertEqual(loop._redact('x', [None, 5]), 'x')

    def test_call_tool_deadline_and_errors(self):
        import time

        slow = loop._call_tool(lambda: time.sleep(1) or {}, {}, deadline=0.05)
        self.assertIn('timed out', slow['error'])

        def boom():
            raise ValueError('nope')

        self.assertEqual(loop._call_tool(boom, {}), {'error': 'nope'})
        self.assertEqual(loop._call_tool(lambda a: {'a': a}, {'a': 1}), {'a': 1})

    def test_subagent_status_tool_is_available_to_loop(self):
        role = get_role('explorer')
        replies = iter([
            json.dumps({'tool': 'subagent_status', 'args': {'task_id': 't_1'}}),
            json.dumps({'final': '- finding: continued'}),
        ])
        seen_messages = []

        def sampling(_system, messages, _max):
            seen_messages.append(list(messages))
            return next(replies)

        result = loop.run_loop('sampling', role, 't', None, None, sampling_fn=sampling)
        self.assertIn('continued', result[0])
        self.assertIn('unknown_task_id', seen_messages[-1][-1]['content'])

    def test_tool_outside_allowlist_still_raises(self):
        def sampling(_system, _messages, _max):
            return json.dumps({'tool': 'apply_edits', 'args': {}})

        with self.assertRaises(RuntimeError) as ctx:
            loop.run_loop('sampling', get_role('explorer'), 't', None, None, sampling_fn=sampling)
        self.assertIn('outside role allowlist', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
