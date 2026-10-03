"""First-run preferences, bounded read-only status checks and CLI discovery."""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

_IMPORT_HOME = tempfile.TemporaryDirectory(prefix='blades-onboarding-import-')
with mock.patch.dict(os.environ, {'BLADES_HOME': _IMPORT_HOME.name}):
    from blades import config, onboarding, providers


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blades-onboarding-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'private state'
        self.state.mkdir()
        self.candidates = {name: [{'path': str(self.root / (name + ' executable')), 'source': 'test'}]
                           for name in onboarding.SUPPORTED}
        self.patches = [mock.patch.object(config, 'CONFIG_DIR', str(self.state)),
                        mock.patch.object(config, 'HOME', str(self.root)),
                        mock.patch.object(onboarding, '_auth_cache', {}),
                        mock.patch.object(providers, 'discover', side_effect=lambda name: self.candidates.get(name, [])),
                        mock.patch.object(onboarding, '_check_auth', return_value={'state': 'unknown', 'detail': 'Check sign-in'})]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_first_run_requires_choices_and_does_not_write_configuration(self):
        result = onboarding.status()
        self.assertFalse(result['completed'])
        self.assertEqual(result['enabled_providers'], [])
        self.assertEqual([p['id'] for p in result['providers']], ['claude', 'codex', 'gemini', 'ollama'])
        self.assertTrue(all(p['available'] and not p['enabled'] for p in result['providers']))
        self.assertFalse((self.state / 'onboarding.json').exists())
        self.assertTrue(all(p['install_url'].startswith('https://') for p in result['providers']))

    def test_enablement_survives_roundtrip_without_launching_or_signing_in(self):
        with mock.patch.object(subprocess, 'run') as run:
            result = onboarding.configure({'enabled_providers': ['gemini', 'claude', 'claude']})
        run.assert_not_called()
        self.assertTrue(result['completed'])
        self.assertEqual(result['enabled_providers'], ['claude', 'gemini'])
        self.assertTrue(onboarding.enabled('claude'))
        self.assertFalse(onboarding.enabled('codex'))
        self.assertEqual(stat.S_IMODE((self.state / 'onboarding.json').stat().st_mode), 0o600)
        self.assertEqual({p['id']: p['enabled'] for p in providers.available()},
                         {'claude': True, 'codex': False, 'gemini': True, 'ollama': False})

    def test_reset_clears_only_choices_and_keeps_sessions_archives_settings_and_accounts(self):
        kept = {'blades.db': b'SESSIONS', 'defaults.json': b'{"port":12345}',
                'projects/project.json': b'PROJECT', 'handoff/archive/session/transcript.json': b'ARCHIVE',
                'run/active.sh': b'RUNNING'}
        for name, body in kept.items():
            file = self.state / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(body)
        credentials = self.root / '.gemini/oauth_creds.json'
        credentials.parent.mkdir()
        credentials.write_text('PRIVATE CREDENTIALS')
        onboarding.configure({'enabled_providers': ['codex']})
        before_port = config.PORT
        result = onboarding.reset()
        self.assertFalse(result['completed'])
        self.assertEqual(config.PORT, before_port)
        self.assertEqual(credentials.read_text(), 'PRIVATE CREDENTIALS')
        for name, body in kept.items():
            self.assertEqual((self.state / name).read_bytes(), body)
        self.assertFalse((self.state / 'onboarding.json').exists())

    def test_invalid_or_unsupported_choices_never_replace_saved_choices(self):
        onboarding.configure({'enabled_providers': ['claude']})
        before = (self.state / 'onboarding.json').read_bytes()
        for value in (None, [], {'enabled_providers': []}, {'enabled_providers': ['copilot']},
                      {'enabled_providers': [None]}, {'enabled_providers': 'claude'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                onboarding.configure(value)
        self.assertEqual((self.state / 'onboarding.json').read_bytes(), before)
        self.candidates['gemini'] = []
        with self.assertRaisesRegex(ValueError, 'not available'):
            onboarding.configure({'enabled_providers': ['gemini']})

    def test_corrupt_preferences_route_back_to_setup(self):
        path = self.state / 'onboarding.json'
        for data in ('bad JSON', '[]', '{"completed":true}',
                     '{"version":1,"completed":true,"enabled_providers":["unsupported"]}'):
            path.write_text(data)
            self.assertFalse(onboarding.preferences()['completed'])
            self.assertEqual(onboarding.preferences()['enabled_providers'], [])

    def test_only_discovered_executable_choices_are_accepted_and_disappearance_fails_closed(self):
        second = str(self.root / "second codex ' binary")
        self.candidates['codex'].append({'path': second, 'source': 'npm'})
        result = onboarding.configure({'enabled_providers': ['codex'], 'executable_overrides': {'codex': second}})
        self.assertEqual(providers.executable('codex'), second)
        entry = next(item for item in result['providers'] if item['id'] == 'codex')
        import shlex
        self.assertEqual(shlex.split(entry['auth_command']), [second, 'login'])
        with self.assertRaisesRegex(ValueError, 'discovery'):
            onboarding.configure({'enabled_providers': ['codex'], 'executable_overrides': {'codex': '/tmp/arbitrary-command'}})
        self.candidates['codex'].pop()
        self.assertIsNone(providers.executable('codex'))
        self.assertTrue(onboarding.enabled('codex'))

    def test_unsupported_cli_discovery_is_informational_only(self):
        self.candidates['copilot'] = [{'path': '/example/copilot', 'source': 'test'}]
        result = onboarding.status()
        self.assertEqual(result['other_clis'][0]['id'], 'copilot')
        self.assertFalse(result['other_clis'][0]['supported'])
        self.assertIsNone(providers.executable('copilot'))

    def test_auth_cache_is_reused_and_explicit_recheck_refreshes_it(self):
        onboarding.auth_status('claude', '/example/claude')
        onboarding.auth_status('claude', '/example/claude')
        self.assertEqual(onboarding._check_auth.call_count, 1)
        onboarding.auth_status('claude', '/example/claude', refresh=True)
        self.assertEqual(onboarding._check_auth.call_count, 2)


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blades-auth-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch = mock.patch.object(config, 'HOME', str(self.root))
        patch.start()
        self.addCleanup(patch.stop)

    def test_claude_status_reduces_account_output_to_a_safe_summary(self):
        raw = json.dumps({'loggedIn': True, 'email': 'PRIVATE EMAIL', 'orgId': 'PRIVATE ORG', 'apiKey': 'PRIVATE KEY'})
        with mock.patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, raw, '')) as run:
            result = onboarding._check_auth('claude', '/path with spaces/claude')
        self.assertEqual(result['state'], 'ready')
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(run.call_args.args[0], ['/path with spaces/claude', 'auth', 'status', '--json'])
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
        self.assertLessEqual(run.call_args.kwargs['timeout'], 5)
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_codex_status_does_not_leak_keys_and_does_not_confuse_not_logged_in(self):
        for code, output, expected in ((0, 'Logged in using an API key - PRIVATE KEY', 'ready'),
                                      (1, 'Not logged in', 'sign_in'),
                                      (1, 'Error: PRIVATE ACCOUNT', 'unknown')):
            with self.subTest(expected=expected), mock.patch.object(subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], code, '', output)) as run:
                result = onboarding._check_auth('codex', '/example/codex')
            self.assertEqual(result['state'], expected)
            self.assertNotIn('PRIVATE', json.dumps(result))
            self.assertEqual(run.call_args.args[0], ['/example/codex', 'login', 'status'])

    def test_timeout_or_missing_cli_never_starts_login(self):
        with mock.patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('status', 4)) as run:
            self.assertEqual(onboarding._check_auth('codex', '/example/codex')['state'], 'unknown')
        self.assertEqual(run.call_count, 1)
        with mock.patch.object(subprocess, 'run') as run:
            self.assertEqual(onboarding._check_auth('claude', None)['state'], 'unknown')
        run.assert_not_called()

    def test_gemini_checks_presence_only_never_contents_and_never_claims_ready(self):
        folder = self.root / '.gemini'
        folder.mkdir()
        credentials = folder / 'oauth_creds.json'
        keys = {'GEMINI_CLI_HOME': str(self.root), 'GEMINI_API_KEY': '', 'GOOGLE_API_KEY': '',
                'GOOGLE_APPLICATION_CREDENTIALS': '', 'GOOGLE_GENAI_USE_VERTEXAI': ''}
        with mock.patch.dict(os.environ, keys), mock.patch.object(Path, 'home', return_value=self.root), \
                mock.patch.object(subprocess, 'run') as run:
            self.assertEqual(onboarding._check_auth('gemini', '/example/gemini')['state'], 'sign_in')
            credentials.write_text('PRIVATE CREDENTIALS')
            with mock.patch.object(Path, 'open', side_effect=AssertionError('credential contents must not be read')):
                result = onboarding._check_auth('gemini', '/example/gemini')
            self.assertEqual(result['state'], 'unknown')
            self.assertNotIn('PRIVATE', json.dumps(result))
            run.assert_not_called()


class DiscoveryTests(unittest.TestCase):
    def test_discovers_non_path_npm_and_native_installation_without_running_commands(self):
        with tempfile.TemporaryDirectory(prefix='blades-discover-') as folder:
            home = Path(folder)
            for relative in ('.local/bin/claude', '.npm-global/bin/gemini', '.nvm/versions/node/v22/bin/codex'):
                file = home / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('#!/bin/sh\nexit 0\n')
                file.chmod(0o700)
            def which(value):
                path = Path(value)
                return str(path) if path.is_relative_to(home) and path.is_file() else None
            with mock.patch.object(Path, 'home', return_value=home), \
                    mock.patch.object(providers.shutil, 'which', side_effect=which), \
                    mock.patch.object(subprocess, 'run') as run:
                self.assertEqual(providers.discover('claude')[0]['path'], str(home / '.local/bin/claude'))
                self.assertEqual(providers.discover('gemini')[0]['source'], 'npm')
                self.assertEqual(providers.discover('codex')[0]['source'], 'nvm')
                self.assertEqual(providers.discover('unknown'), [])
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
