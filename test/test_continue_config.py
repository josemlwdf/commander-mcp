import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from lib.subagents import continue_config as cc

try:
    import yaml
except ImportError:
    yaml = None

KEY = 'sk-test-AAAA'
HDR = 'sk-test-HDR1'
Q = chr(34)


def write_config(directory, name, data):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if name.endswith('.json'):
        path.write_text(json.dumps(data), encoding='utf-8')
    else:
        path.write_text(data, encoding='utf-8')
    return path


def entry(**kw):
    base = {'name': 'm', 'provider': 'openai', 'model': 'gpt-x', 'apiKey': KEY}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


class Base(unittest.TestCase):
    def setUp(self):
        cc.clear_cache()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / 'home'
        self.ws = self.root / 'ws'
        self.home.mkdir()
        self.ws.mkdir()
        self.cdir = self.home / '.continue'
        # Keep tests hermetic: never read the real Windows profiles under /mnt/c.
        empty_users = self.root / 'no-wsl-users'
        empty_users.mkdir()
        old_users_root = cc.WSL_USERS_ROOT
        cc.WSL_USERS_ROOT = empty_users
        self.addCleanup(setattr, cc, 'WSL_USERS_ROOT', old_users_root)

    def models(self, entries, env=None, directory=None, refs=True):
        return cc.list_usable_models(
            {'models': entries}, directory or self.cdir, env or {}, refs
        )


class PathTests(Base):
    def test_order_and_dedupe(self):
        env = {
            'SUBAGENT_CONTINUE_CONFIG': str(self.root / 'custom.json'),
            'CONTINUE_GLOBAL_DIR': str(self.root / 'g'),
            'USERPROFILE': str(self.root / 'win'),
            'USER': 'no-such-user-xyz',
        }
        paths = cc.candidate_config_paths(self.ws, env, self.home)
        self.assertEqual(paths, [
            self.root / 'custom.json',
            self.root / 'g' / 'config.yaml',
            self.cdir / 'config.yaml',
            self.cdir / 'config.json',
            self.root / 'win' / '.continue' / 'config.yaml',
            self.root / 'win' / '.continue' / 'config.json',
            self.ws / '.continue' / 'config.yaml',
        ])

    def test_duplicates_removed(self):
        env = {'CONTINUE_GLOBAL_DIR': str(self.cdir), 'USERPROFILE': str(self.home)}
        paths = cc.candidate_config_paths(self.ws, env, self.home)
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(paths.count(self.cdir / 'config.yaml'), 1)

    def test_no_wsl_path_when_dir_missing_or_user_unsafe(self):
        for user in ('no-such-user-xyz', 'a/b', '..'):
            paths = cc.candidate_config_paths(self.ws, {'USER': user}, self.home)
            self.assertFalse(any(str(p).startswith('/mnt/c/Users') for p in paths), user)


class WslEnumerationTests(Base):
    def setUp(self):
        super().setUp()
        self.users = self.root / 'users'
        self.users.mkdir()
        old = cc.WSL_USERS_ROOT
        cc.WSL_USERS_ROOT = self.users
        self.addCleanup(setattr, cc, 'WSL_USERS_ROOT', old)

    def wsl_paths(self, env=None):
        paths = cc.candidate_config_paths(self.ws, env or {}, self.home)
        return [p for p in paths if str(p).startswith(str(self.users))]

    def test_other_windows_user_is_found(self):
        (self.users / 'Alice' / '.continue').mkdir(parents=True)
        paths = self.wsl_paths({'USER': 'think'})
        self.assertEqual(paths, [
            self.users / 'Alice' / '.continue' / 'config.yaml',
            self.users / 'Alice' / '.continue' / 'config.json',
        ])

    def test_current_user_first_case_insensitive(self):
        (self.users / 'Alice' / '.continue').mkdir(parents=True)
        (self.users / 'Bob' / '.continue').mkdir(parents=True)
        paths = self.wsl_paths({'USER': 'bob'})
        self.assertEqual(paths[0], self.users / 'Bob' / '.continue' / 'config.yaml')
        self.assertEqual(paths[2], self.users / 'Alice' / '.continue' / 'config.yaml')

    def test_system_profiles_and_missing_continue_dir_skipped(self):
        for name in ('Public', 'Default', 'Default User', 'All Users'):
            (self.users / name / '.continue').mkdir(parents=True)
        (self.users / 'NoContinue').mkdir()
        (self.users / 'file.txt').write_text('x', encoding='utf-8')
        self.assertEqual(self.wsl_paths({'USER': 'x'}), [])

    def test_symlinked_profile_skipped(self):
        real = self.root / 'elsewhere' / '.continue'
        real.mkdir(parents=True)
        try:
            os.symlink(real.parent, self.users / 'Linked')
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        self.assertEqual(self.wsl_paths({'USER': 'x'}), [])

    def test_missing_root_is_empty(self):
        cc.WSL_USERS_ROOT = self.root / 'does-not-exist'
        self.assertEqual(cc._wsl_continue_dirs('think'), [])

    def test_profile_count_is_capped(self):
        for i in range(cc.MAX_WSL_PROFILES + 5):
            (self.users / f'u{i:03d}' / '.continue').mkdir(parents=True)
        self.assertEqual(len(cc._wsl_continue_dirs('')), cc.MAX_WSL_PROFILES)

    def test_config_in_other_profile_resolves(self):
        write_config(self.users / 'Alice' / '.continue', 'config.json',
                     {'models': [entry()]})
        got = cc.resolve_continue_model(self.ws, {'USER': 'think'}, self.home)
        self.assertIsNotNone(got)
        self.assertEqual(got.model.api_key, KEY)


class KeyTests(Base):
    def test_literal_key(self):
        self.assertEqual(self.models([entry()])[0].api_key, KEY)

    def test_bearer_header_any_case(self):
        for name in ('Authorization', 'authorization'):
            e = entry(apiKey=None, requestOptions={'headers': {name: 'Bearer ' + HDR}})
            self.assertEqual(self.models([e])[0].api_key, HDR)

    def test_secret_from_env(self):
        e = entry(apiKey='${{ secrets.MY_KEY }}')
        self.assertEqual(self.models([e], {'MY_KEY': KEY})[0].api_key, KEY)

    def test_secret_from_dotenv(self):
        lines = [
            '# a comment',
            'A=sk-env-A',
            'export B=' + Q + 'sk-env-B' + Q,
        ]
        write_config(self.cdir, '.env', '\n'.join(lines))
        entries = [
            entry(name='a', apiKey='${{ secrets.A }}'),
            entry(name='b', apiKey='${{ secrets.B }}'),
        ]
        got = {m.name: m.api_key for m in self.models(entries)}
        self.assertEqual(got, {'a': 'sk-env-A', 'b': 'sk-env-B'})

    def test_unresolved_secret_skips_model(self):
        e = entry(apiKey='${{ secrets.NOPE }}')
        self.assertEqual(self.models([e]), [])

    def test_unresolved_secret_falls_to_next_model(self):
        entries = [entry(name='x', apiKey='${{ secrets.NOPE }}'), entry(name='y')]
        got = self.models(entries)
        self.assertEqual([m.name for m in got], ['y'])
        self.assertNotIn('secrets', got[0].api_key)

    def test_local_provider_needs_no_key(self):
        m = self.models([entry(provider='ollama', model='llama3', apiKey=None)])
        self.assertEqual(len(m), 1)
        self.assertIsNone(m[0].api_key)

    def test_remote_provider_without_key_skipped(self):
        self.assertEqual(self.models([entry(apiKey=None)]), [])

    def test_untrusted_drops_secret_refs_keeps_literals(self):
        entries = [entry(name='ref', apiKey='${{ secrets.MY_KEY }}'), entry(name='lit')]
        got = self.models(entries, {'MY_KEY': 'sk-env'}, refs=False)
        self.assertEqual([m.name for m in got], ['lit'])


class NormalizeTests(Base):
    def test_skips_invalid_entries(self):
        entries = [
            {'uses': 'foo/bar'},
            'not-a-dict',
            entry(provider=None),
            entry(model=None),
            entry(name='ok'),
        ]
        self.assertEqual([m.name for m in self.models(entries)], ['ok'])

    def test_roles(self):
        entries = [
            entry(name='emb', roles=['embed']),
            entry(name='default'),
            entry(name='chat', roles=['chat', 'edit']),
        ]
        self.assertEqual([m.name for m in self.models(entries)], ['default', 'chat'])

    def test_name_default_and_api_base(self):
        m = self.models([entry(name=None, apiBase='http://h/v1')])[0]
        self.assertEqual(m.name, 'gpt-x')
        self.assertEqual(m.api_base, 'http://h/v1')

    def test_max_tokens(self):
        def mt(v):
            return self.models([entry(defaultCompletionOptions={'maxTokens': v})])[0].max_tokens
        self.assertEqual(mt(512), 512)
        for bad in (True, 0, '512', -1):
            self.assertIsNone(mt(bad))

    def test_headers_only_strings(self):
        e = entry(requestOptions={'headers': {'X-A': 'one', 'X-B': 5}})
        self.assertEqual(self.models([e])[0].headers, {'X-A': 'one'})

    def test_select_model(self):
        ms = self.models([entry(name='Alpha', model='m1'), entry(name='beta', model='M2')])
        self.assertEqual(cc.select_model(ms, None).name, 'Alpha')
        self.assertEqual(cc.select_model(ms, 'alpha').name, 'Alpha')
        self.assertEqual(cc.select_model(ms, 'm2').name, 'beta')
        self.assertIsNone(cc.select_model(ms, 'unknown'))
        self.assertIsNone(cc.select_model([], None))


class LoadTests(Base):
    def test_bad_inputs_return_none(self):
        big = self.root / 'big.json'
        big.write_text('{' + ' ' * (cc.MAX_CONFIG_BYTES + 10) + '}')
        bad = self.root / 'bad.json'
        bad.write_text('{oops')
        top = self.root / 'list.json'
        top.write_text('[1, 2]')
        for p in (big, bad, top, self.root / 'missing.json', self.root):
            self.assertIsNone(cc.load_config_file(p), str(p))

    def test_valid_json(self):
        p = write_config(self.root, 'ok.json', {'models': []})
        self.assertEqual(cc.load_config_file(p), {'models': []})

    @unittest.skipIf(yaml is not None, 'PyYAML installed')
    def test_yaml_without_pyyaml_returns_none(self):
        p = write_config(self.root, 'c.yaml', 'models: []')
        self.assertIsNone(cc.load_config_file(p))

    @unittest.skipIf(yaml is None, 'PyYAML not installed')
    def test_yaml_anchor_and_merge_key(self):
        text = '\n'.join([
            '%YAML 1.1',
            '---',
            'defaults: &d',
            '  provider: openai',
            '  apiKey: ' + KEY,
            'models:',
            '  - <<: *d',
            '    name: anch',
            '    model: gpt-x',
        ])
        p = write_config(self.root, 'c.yaml', text)
        cfg = cc.load_config_file(p)
        got = cc.list_usable_models(cfg, self.root, {})
        self.assertEqual([(m.name, m.api_key) for m in got], [('anch', KEY)])

    @unittest.skipIf(yaml is None, 'PyYAML not installed')
    def test_malformed_yaml_returns_none(self):
        p = write_config(self.root, 'bad.yaml', 'models: [unclosed')
        self.assertIsNone(cc.load_config_file(p))

    @unittest.skipIf(yaml is None, 'PyYAML not installed')
    def test_workspace_config_is_untrusted_via_resolve(self):
        text = '\n'.join([
            'models:',
            '  - name: ref',
            '    provider: openai',
            '    model: a',
            '    apiKey: ${{ secrets.MY_KEY }}',
            '  - name: lit',
            '    provider: openai',
            '    model: b',
            '    apiKey: ' + KEY,
        ])
        write_config(self.ws / '.continue', 'config.yaml', text)
        r = cc.resolve_continue_model(self.ws, {'MY_KEY': 'sk-env'}, self.home)
        self.assertEqual(r.model.name, 'lit')


class ResolveTests(Base):
    def test_resolve_all_models_from_first_usable_config(self):
        write_config(
            self.cdir,
            'config.json',
            {'models': [entry(name='first'), entry(name='second', model='m2')]},
        )
        resolved = cc.resolve_continue_models(self.ws, {}, self.home)
        self.assertEqual([item.model.name for item in resolved], ['first', 'second'])
        self.assertTrue(all(item.config_path == str(self.cdir / 'config.json') for item in resolved))

    def test_first_usable_wins_and_falls_through(self):
        bad = self.root / 'bad.json'
        bad.write_text('{oops')
        write_config(self.cdir, 'config.json', {'models': [entry(name='home')]})
        env = {'SUBAGENT_CONTINUE_CONFIG': str(bad)}
        r = cc.resolve_continue_model(self.ws, env, self.home)
        self.assertEqual(r.model.name, 'home')
        self.assertEqual(r.config_path, str(self.cdir / 'config.json'))

    def test_override_file_wins(self):
        write_config(self.cdir, 'config.json', {'models': [entry(name='home')]})
        custom = write_config(self.root, 'custom.json', {'models': [entry(name='custom')]})
        env = {'SUBAGENT_CONTINUE_CONFIG': str(custom)}
        self.assertEqual(cc.resolve_continue_model(self.ws, env, self.home).model.name, 'custom')

    def test_preferred_model(self):
        write_config(self.cdir, 'config.json', {'models': [entry(name='a'), entry(name='b', model='m2')]})
        env = {'SUBAGENT_CONTINUE_MODEL': 'B'}
        self.assertEqual(cc.resolve_continue_model(self.ws, env, self.home).model.name, 'b')
        env = {'SUBAGENT_CONTINUE_MODEL': 'nope'}
        self.assertIsNone(cc.resolve_continue_model(self.ws, env, self.home))

    def test_nothing_usable(self):
        self.assertIsNone(cc.resolve_continue_model(self.ws, {}, self.home))
        write_config(self.cdir, 'config.json', {'models': [entry(apiKey=None)]})
        self.assertIsNone(cc.resolve_continue_model(self.ws, {}, self.home))

    def test_cache_picks_up_edits(self):
        p = write_config(self.cdir, 'config.json', {'models': [entry(name='one')]})
        os.utime(p, (1000, 1000))
        self.assertEqual(cc.resolve_continue_model(self.ws, {}, self.home).model.name, 'one')
        write_config(self.cdir, 'config.json', {'models': [entry(name='two')]})
        os.utime(p, (2000, 2000))
        self.assertEqual(cc.resolve_continue_model(self.ws, {}, self.home).model.name, 'two')

    def test_no_output(self):
        write_config(self.cdir, 'config.json', {'models': [entry()]})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cc.resolve_continue_model(self.ws, {}, self.home)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), '')


class SecretHygieneTests(Base):
    def test_repr_and_str_hide_secrets(self):
        e = entry(requestOptions={'headers': {'X-Token': HDR}})
        m = self.models([e])[0]
        r = cc.ResolvedContinue(model=m, config_path='/x')
        for obj in (m, r):
            for text in (repr(obj), str(obj)):
                self.assertNotIn(KEY, text)
                self.assertNotIn(HDR, text)

    def test_safe_summary_keys(self):
        m = self.models([entry()])[0]
        self.assertEqual(set(m.safe_summary()), {'provider', 'model', 'name'})
        self.assertNotIn(KEY, str(m.safe_summary()))


if __name__ == '__main__':
    unittest.main()
