"""Setup API and recovery contracts with isolated files and fake CLI launches."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

_IMPORT_HOME = tempfile.TemporaryDirectory(prefix='blades-onboarding-api-import-')
with mock.patch.dict(os.environ, {'BLADES_HOME': _IMPORT_HOME.name}):
    from blades import config, lifecycle, onboarding, projects, providers, security, store, tmuxctl


class OnboardingApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blades-onboarding-api-')
        self.root = Path(self.temp.name)
        self.state = self.root / 'private state'
        for name in ('run', 'projects', 'handoff/archive'):
            (self.state / name).mkdir(parents=True, exist_ok=True)
        self.project_dir = self.root / 'Project with spaces'
        self.project_dir.mkdir()
        self.codex_home = self.root / '.codex'
        self.codex_home.mkdir()
        self.patches = [
            mock.patch.object(config, 'CONFIG_DIR', str(self.state)),
            mock.patch.object(config, 'HOME', str(self.root)),
            mock.patch.object(config, 'PROJECTS_MD', str(self.root / 'unused-projects.md')),
            mock.patch.object(config, 'tailscale_ip', return_value=''),
            mock.patch.object(config, 'bind_addresses', return_value=['127.0.0.1']),
            mock.patch.object(security, 'allowed_hosts', return_value={'localhost'}),
            mock.patch.object(store, '_conn', None),
            mock.patch.object(store, '_blade_locks', {}),
            mock.patch.object(projects, '_cache', {}),
            mock.patch.object(onboarding, '_auth_cache', {}),
            mock.patch.dict(os.environ, {'CODEX_HOME': str(self.codex_home)}),
            mock.patch.object(onboarding, '_check_auth', return_value={'state': 'unknown', 'detail': 'Check sign-in'}),
            mock.patch.object(providers, 'discover', side_effect=lambda name: [
                {'path': str(self.root / name), 'source': 'Fake CLI'}] if name in onboarding.SUPPORTED else []),
            mock.patch.object(tmuxctl, 'list_sessions', side_effect=lambda: [b['name'] for b in store.live()]),
            mock.patch.object(tmuxctl, 'pane_dead', return_value=False),
            mock.patch.object(tmuxctl, 'new_session'),
            mock.patch.object(tmuxctl, 'send_text'),
            mock.patch.object(tmuxctl, 'capture_all', return_value='Retained terminal history'),
        ]
        for patch in self.patches:
            patch.start()
        self.module = importlib.import_module('blades.app')
        self.receipts = mock.patch.object(self.module, '_handoff_receipts', {})
        self.receipts.start()
        self.client = self.module.app.test_client()
        self.headers = {'X-Blades-Request': '1', 'Origin': 'http://localhost'}
        response = self.client.post('/api/projects', json={'path': str(self.project_dir), 'name': 'Example'}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.slug = response.json['slug']

    def tearDown(self):
        if store._conn:
            store._conn.close()
        self.receipts.stop()
        for patch in reversed(self.patches):
            patch.stop()
        self.temp.cleanup()

    def post(self, endpoint, body=None):
        return self.client.post(endpoint, json={} if body is None else body, headers=self.headers)

    def launch(self, provider):
        return self.post('/api/blades', {'slug': self.slug, 'provider': provider})

    def test_first_run_prevents_new_assistant_processes_until_choices_are_saved(self):
        status = self.client.get('/api/onboarding')
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json['completed'])
        self.assertEqual(status.json['enabled_providers'], [])
        for provider in onboarding.SUPPORTED:
            with self.subTest(provider=provider):
                self.assertEqual(self.launch(provider).status_code, 409)
        tmuxctl.new_session.assert_not_called()
        self.assertEqual(store.live(), [])

    def test_selecting_one_provider_allows_only_that_new_assistant(self):
        result = self.post('/api/onboarding', {'enabled_providers': ['codex']})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertTrue(result.json['completed'])
        self.assertEqual(result.json['enabled_providers'], ['codex'])
        self.assertEqual(self.launch('claude').status_code, 409)
        self.assertEqual(self.launch('gemini').status_code, 409)
        opened = self.launch('codex')
        self.assertEqual(opened.status_code, 200, opened.json)
        self.assertEqual(opened.json['provider'], 'codex')
        tmuxctl.new_session.assert_called_once()
        self.assertEqual(tmuxctl.new_session.call_args.kwargs['provider'], 'codex')
        enabled = {item['id']: item['enabled'] for item in self.client.get('/api/providers').json}
        self.assertEqual(enabled, {'claude': False, 'codex': True, 'gemini': False})

    def test_reset_preserves_active_interaction_archived_resume_and_host_data(self):
        self.assertEqual(self.post('/api/onboarding', {'enabled_providers': ['codex']}).status_code, 200)
        active = self.launch('codex').json
        store.set_state(active['name'], 'idle', note='Active conversation remains')
        archived_name = 'blade-example-2'
        sid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        source = self.codex_home / 'sessions' / ('rollout-' + sid + '.jsonl')
        source.parent.mkdir()
        content = json.dumps({'type': 'session_meta', 'payload': {'id': sid, 'cwd': str(self.project_dir), 'source': 'cli'}}) + '\n'
        source.write_text(content)
        store.create(archived_name, self.slug, 'Example', str(self.project_dir), provider='codex')
        store.mark_activity(archived_name, 'idle', 'codex:turn-complete', session_id=sid, transcript_path=str(source))
        archive = Path(lifecycle.snapshot(archived_name, reason='test-before-reset'))
        store.set_state(archived_name, 'closed')
        source.unlink()  # Resume must recover the archive after reset.
        credential = self.codex_home / 'auth.json'
        credential.write_text('PRIVATE AUTH SENTINEL')
        config.save_json(config.path('defaults.json'), {'idle_timeout_min': 321})
        config.save_json(config.path('server.json'), {'port': config.PORT})
        config.save_project_config(self.slug, {'notes': 'Saved project notes'})
        files = [self.state / 'projects.json', self.state / 'projects' / (self.slug + '.json'),
                 self.state / 'defaults.json', self.state / 'server.json', credential,
                 archive / 'transcript.jsonl', archive / 'metadata.json', archive / 'terminal.txt']
        preserved = {file: file.read_bytes() for file in files}
        before_port = config.PORT

        reset = self.post('/api/onboarding/reset')
        self.assertEqual(reset.status_code, 200, reset.json)
        self.assertFalse(reset.json['completed'])
        self.assertEqual(config.PORT, before_port)
        for file, body in preserved.items():
            self.assertEqual(file.read_bytes(), body)
        self.assertEqual(store.get(active['name'])['state'], 'idle')
        self.assertEqual(store.get(archived_name)['state'], 'closed')
        self.assertEqual(self.launch('codex').status_code, 409)

        sent = self.post('/api/blades/' + active['name'] + '/send', {'text': 'Continue my existing task'})
        self.assertEqual(sent.status_code, 200, sent.json)
        tmuxctl.send_text.assert_called_once_with(active['name'], 'Continue my existing task', enter=True)
        listed = self.client.get('/api/projects')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json[0]['slug'], self.slug)
        status = self.client.get('/api/status')
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json['idle_timeout_min'], 321)
        self.assertNotIn('PRIVATE AUTH', json.dumps(reset.json) + json.dumps(status.json))

        resumed = self.post('/api/blades/' + archived_name + '/resume')
        self.assertEqual(resumed.status_code, 200, resumed.json)
        self.assertEqual(source.read_text(), content)
        self.assertEqual(resumed.json['session_id'], sid)
        self.assertNotEqual(resumed.json['name'], archived_name)
        self.assertEqual(tmuxctl.new_session.call_args.kwargs['resume_id'], sid)
        self.assertEqual(tmuxctl.new_session.call_args.kwargs['provider'], 'codex')
        self.assertEqual(tmuxctl.new_session.call_args.args[2], '')
        self.assertEqual(store.get(archived_name)['state'], 'closed')

    def test_setup_mutations_require_json_non_simple_header_and_matching_origin(self):
        for endpoint, body in (('/api/onboarding', {'enabled_providers': ['claude']}),
                               ('/api/onboarding/reset', {})):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.post(endpoint, json=body).status_code, 403)
                self.assertEqual(self.client.post(endpoint, json=body,
                    headers={'X-Blades-Request': '1', 'Origin': 'http://hostile.test'}).status_code, 403)
                self.assertEqual(self.client.post(endpoint, data='enabled_providers=claude',
                    content_type='application/x-www-form-urlencoded', headers=self.headers).status_code, 415)
        self.assertFalse((self.state / 'onboarding.json').exists())
        self.assertEqual(self.post('/api/onboarding', {'enabled_providers': ['copilot']}).status_code, 400)

    def test_recheck_refreshes_only_read_only_status_probes(self):
        self.client.get('/api/onboarding')
        calls = onboarding._check_auth.call_count
        self.client.get('/api/onboarding')
        self.assertEqual(onboarding._check_auth.call_count, calls)
        self.client.get('/api/onboarding?refresh=1')
        self.assertEqual(onboarding._check_auth.call_count, calls + 3)
        tmuxctl.new_session.assert_not_called()


class SelectedRuntimeTests(unittest.TestCase):
    def test_status_uses_selected_cli_sibling_runtime_without_changing_parent_path(self):
        with tempfile.TemporaryDirectory(prefix='blades-runtime-') as directory:
            root = Path(directory)
            selected = root / 'nvm' / 'versions' / 'node' / 'v22' / 'bin'
            selected.mkdir(parents=True)
            cli = selected / 'claude'
            cli.write_text('#!/usr/bin/env node\n// fake CLI for runtime-selection test\n')
            cli.chmod(0o700)
            runtime = selected / 'node'
            runtime.write_text('#!/bin/sh\nprintf \'{"loggedIn":true}\\n\'\n')
            runtime.chmod(0o700)
            original = '/usr/bin:/bin'
            with mock.patch.dict(os.environ, {'PATH': original}), mock.patch.object(config, 'HOME', str(root)):
                result = onboarding._check_auth('claude', str(cli))
                self.assertEqual(result['state'], 'ready')
                self.assertEqual(os.environ['PATH'], original)
                child = providers.environment_for(str(cli))
                self.assertEqual(child['PATH'].split(os.pathsep)[0], str(selected))
                self.assertEqual(child['PATH'].split(os.pathsep)[1:], ['/usr/bin', '/bin'])


if __name__ == '__main__':
    unittest.main()
