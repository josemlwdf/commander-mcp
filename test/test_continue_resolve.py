import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import support
from lib.core import system_info
from lib.subagents import continue_config
from lib.subagents.resolve import resolve_backend, resolve_continue

KEY = 'sk-resolve-ZZZZ'


class ResolveContinueTests(unittest.TestCase):
    def setUp(self):
        continue_config.clear_cache()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = self.root / 'config.json'
        self._isolate_wsl(self.root)
        self.write([{'name': 'main', 'provider': 'openai', 'model': 'gpt-x', 'apiKey': KEY}])

    def _isolate_wsl(self, root):
        # Hermetic: never read the real Windows profiles under /mnt/c.
        empty = Path(root) / 'no-wsl-users'
        empty.mkdir()
        old = continue_config.WSL_USERS_ROOT
        continue_config.WSL_USERS_ROOT = empty
        self.addCleanup(setattr, continue_config, 'WSL_USERS_ROOT', old)
        # Also pin the home directory so a real ~/.continue is never read.
        fake_home = Path(root) / 'home'
        fake_home.mkdir()
        home_patch = patch.object(Path, 'home', return_value=fake_home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        # The server's workspace root can be the user's real home; pin it too.
        root_patch = patch.object(support, 'ROOT', str(root), create=True)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def write(self, models):
        self.config.write_text(json.dumps({'models': models}), encoding='utf-8')
        os.utime(self.config, None)
        continue_config.clear_cache()

    def env(self, **extra):
        values = {'SUBAGENT_CONTINUE_CONFIG': str(self.config)}
        values.update(extra)
        return patch.dict(os.environ, values, clear=True)

    def test_auto_is_default_on(self):
        with self.env():
            self.assertEqual(resolve_backend({}), 'continue:openai')

    def test_auto_with_opt_in_uses_continue(self):
        with self.env(SUBAGENT_CONTINUE='1'):
            self.assertEqual(resolve_backend({}), 'continue:openai')

    def test_explicit_opt_out_values_disable_discovery(self):
        for value in ('0', 'false', 'FALSE', 'no', 'off', ' 0 '):
            with self.env(SUBAGENT_CONTINUE=value):
                self.assertEqual(resolve_backend({}), 'cooperative', value)

    def test_opt_out_does_not_block_explicit_continue_mode(self):
        with self.env(SUBAGENT_CONTINUE='0', SUBAGENT_BACKEND='continue'):
            self.assertEqual(resolve_backend({}), 'continue:openai')

    def test_sampling_and_direct_keys_take_precedence(self):
        with self.env(SUBAGENT_CONTINUE='1'):
            self.assertEqual(resolve_backend({'sampling': True}), 'sampling')
        with self.env(SUBAGENT_CONTINUE='1', OPENAI_API_KEY='x'):
            self.assertEqual(resolve_backend({}), 'direct:openai')

    def test_explicit_mode_is_its_own_opt_in(self):
        with self.env(SUBAGENT_BACKEND='continue'):
            self.assertEqual(resolve_backend({}), 'continue:openai')

    def test_no_usable_config_falls_back_to_cooperative(self):
        self.write([{'name': 'nokey', 'provider': 'openai', 'model': 'm'}])
        with self.env(SUBAGENT_CONTINUE='1'):
            self.assertEqual(resolve_backend({}), 'cooperative')
        with self.env(SUBAGENT_BACKEND='continue'):
            self.assertEqual(resolve_backend({}), 'cooperative')
            self.assertIsNone(resolve_continue())

    def test_unsupported_provider_is_skipped_for_next_model(self):
        self.write([
            {'name': 'g', 'provider': 'gemini', 'model': 'gem', 'apiKey': KEY},
            {'name': 'a', 'provider': 'anthropic', 'model': 'claude', 'apiKey': KEY},
        ])
        with self.env(SUBAGENT_CONTINUE='1'):
            self.assertEqual(resolve_backend({}), 'continue:anthropic')
            self.assertEqual(resolve_continue().model.name, 'a')

    def test_only_unsupported_provider_falls_back(self):
        self.write([{'name': 'g', 'provider': 'gemini', 'model': 'gem', 'apiKey': KEY}])
        with self.env(SUBAGENT_CONTINUE='1'):
            self.assertEqual(resolve_backend({}), 'cooperative')

    def test_preferred_model_env(self):
        self.write([
            {'name': 'big', 'provider': 'openai', 'model': 'gpt-big', 'apiKey': KEY},
            {'name': 'cheap', 'provider': 'ollama', 'model': 'llama3'},
        ])
        with self.env(SUBAGENT_CONTINUE='1', SUBAGENT_CONTINUE_MODEL='cheap'):
            self.assertEqual(resolve_backend({}), 'continue:ollama')

    def test_broken_config_never_raises(self):
        self.config.write_text('{oops', encoding='utf-8')
        continue_config.clear_cache()
        with self.env(SUBAGENT_CONTINUE='1'):
            self.assertEqual(resolve_backend({}), 'cooperative')


class CapabilitiesTests(unittest.TestCase):
    def setUp(self):
        continue_config.clear_cache()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = Path(tmp.name) / 'config.json'
        empty = Path(tmp.name) / 'no-wsl-users'
        empty.mkdir()
        old = continue_config.WSL_USERS_ROOT
        continue_config.WSL_USERS_ROOT = empty
        self.addCleanup(setattr, continue_config, 'WSL_USERS_ROOT', old)
        fake_home = Path(tmp.name) / 'home'
        fake_home.mkdir()
        home_patch = patch.object(Path, 'home', return_value=fake_home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        root_patch = patch.object(support, 'ROOT', tmp.name, create=True)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.config.write_text(json.dumps({'models': [
            {'name': 'main', 'provider': 'openai', 'model': 'gpt-x', 'apiKey': KEY,
             'requestOptions': {'headers': {'X-Token': 'hdr-secret-9999'}}},
        ]}), encoding='utf-8')

    def test_reports_provider_model_path_and_never_secrets(self):
        env = {'SUBAGENT_CONTINUE_CONFIG': str(self.config), 'SUBAGENT_CONTINUE': '1'}
        with patch.dict(os.environ, env, clear=True):
            caps = system_info.get_capabilities()
        info = caps['subagents']['continue']
        self.assertEqual(caps['subagents']['backend'], 'continue:openai')
        self.assertEqual(info, {
            'provider': 'openai', 'model': 'gpt-x', 'name': 'main',
            'config_path': str(self.config),
        })
        dumped = json.dumps(caps)
        self.assertNotIn(KEY, dumped)
        self.assertNotIn('hdr-secret-9999', dumped)
        self.assertTrue(caps['env']['SUBAGENT_CONTINUE'])

    def test_no_continue_section_when_opted_out(self):
        env = {'SUBAGENT_CONTINUE_CONFIG': str(self.config), 'SUBAGENT_CONTINUE': '0'}
        with patch.dict(os.environ, env, clear=True):
            caps = system_info.get_capabilities()
        self.assertNotIn('continue', caps['subagents'])
        self.assertNotIn(KEY, json.dumps(caps))


if __name__ == '__main__':
    unittest.main()
