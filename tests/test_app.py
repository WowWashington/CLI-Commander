"""API boundary and orchestration tests, with isolated state and no live CLIs."""
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ApiTest(unittest.TestCase):
    def setUp(self):
        from blades import config, store
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.config, self.store = config, store
        if store._conn:
            store._conn.close()
            store._conn = None
        self.patch = patch.object(config, 'CONFIG_DIR', str(self.home))
        self.patch.start()
        for directory in ('projects', 'handoff/archive', 'run'):
            (self.home / directory).mkdir(parents=True, exist_ok=True)
        self.mod = importlib.import_module('blades.app')
        self.hosts = patch('blades.security.allowed_hosts', return_value={'localhost'})
        self.hosts.start()
        self.client = self.mod.app.test_client()
        self.headers = {'X-Blades-Request': '1'}
        self.project = {'slug': 'demo', 'name': 'Demo', 'path': str(self.home), 'exists': True}
        config.save_json(config.path('onboarding.json'), {'version': 1, 'completed': True,
                                                        'enabled_providers': ['claude', 'codex', 'gemini']})

    def tearDown(self):
        self.hosts.stop()
        if self.store._conn:
            self.store._conn.close()
            self.store._conn = None
        self.patch.stop()
        self.tmp.cleanup()

    def blade(self, provider='claude'):
        return self.store.create('blade-demo-1', 'demo', 'Demo', str(self.home), provider=provider)

    def test_ui_version_matches_assets_and_changes_with_each_source(self):
        static = self.home / 'static'
        static.mkdir()
        for filename in ('index.html', 'app.js', 'app.css'):
            (static / filename).write_bytes((Path(self.mod.STATIC) / filename).read_bytes())
        with patch.object(self.mod, 'STATIC', str(static)), \
             patch.object(self.mod.app, '_static_folder', str(static)), \
             patch('blades.config.bind_addresses', return_value=['127.0.0.1']), \
             patch('blades.config.tailscale_ip', return_value=None), \
             patch('blades.providers.available', return_value=[]):
            def check_version():
                status = self.client.get('/api/status')
                self.assertEqual(status.status_code, 200)
                version = status.json['ui_version']
                self.assertRegex(version, r'^[0-9a-f]{16}$')
                response = self.client.get('/')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                html = response.get_data(as_text=True)
                self.assertIn(f'<meta name="ui-version" content="{version}">', html)
                self.assertNotIn('__UI_VERSION__', html)
                for filename in ('app.js', 'app.css'):
                    url = f'/static/{filename}?v={version}'
                    self.assertIn(url, html)
                    with self.client.get(url) as asset:
                        self.assertEqual(asset.status_code, 200)
                        self.assertEqual(asset.data, (static / filename).read_bytes())
                return version

            previous = check_version()
            self.assertEqual(previous, check_version())
            for filename in ('app.js', 'app.css', 'index.html'):
                with self.subTest(filename=filename):
                    with (static / filename).open('a') as source:
                        source.write('\n')
                    current = check_version()
                    self.assertNotEqual(previous, current)
                    previous = current

    def test_terminal_queries_do_not_count_as_human_input(self):
        for reply in (b'\x1b[?1;2c', b'\x1b[>0;276;0c', b'\x1b[1;1R',
                      b'\x1b]11;rgb:0000/0000/0000\x1b\\', b'\x1bP1+r544e=787465726d\x1b\\'):
            self.assertTrue(self.mod._is_terminal_reply(reply), reply)
        for typed in (b'hello', b'\r', b'\x03', b'\x1b[A', b'\t'):
            self.assertFalse(self.mod._is_terminal_reply(typed), typed)

    def test_unregistered_tmux_targets_are_unreachable(self):
        with patch('blades.tmuxctl.capture') as capture:
            response = self.client.get('/api/blades/sensitive-personal-session/screen')
            self.assertEqual(response.status_code, 404)
            capture.assert_not_called()

    def test_open_failure_retains_handoff(self):
        note = self.home / 'handoff/demo.md'
        note.write_text('Keep this note\n')
        with patch('blades.projects.by_slug', return_value=self.project), \
             patch('blades.prompts.render', return_value='Opening prompt'), \
             patch('blades.providers.executable', return_value='/fake/claude'), \
             patch('blades.tmuxctl.next_name', return_value='blade-demo-1'), \
             patch('blades.tmuxctl.new_session', side_effect=RuntimeError('missing CLI')):
            response = self.client.post('/api/blades', json={'slug': 'demo'}, headers=self.headers)
        self.assertEqual(response.status_code, 500)
        self.assertTrue(note.exists())
        self.assertEqual(self.store.get('blade-demo-1')['state'], 'closed')

    def test_session_start_archives_only_matching_handoff(self):
        import hashlib
        b = self.blade()
        note = self.home / 'handoff/demo.md'
        note.write_text('Keep this note\n')
        self.mod._handoff_receipts[b['name']] = hashlib.sha256(note.read_bytes()).hexdigest()
        response = self.client.post('/hook', json={'blade': b['name'], 'event': 'SessionStart',
                                                   'payload': {'session_id': 'exact-id'}})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(note.exists())
        self.assertEqual(len(list((self.home/'handoff/archive').glob('*.md'))), 1)

    def test_newer_handoff_survives_late_start(self):
        b = self.blade()
        note = self.home / 'handoff/demo.md'
        note.write_text('A newer note\n')
        self.mod._handoff_receipts[b['name']] = 'old-digest'
        self.client.post('/hook', json={'blade': b['name'], 'event': 'SessionStart', 'payload': {}})
        self.assertTrue(note.exists())

    def test_force_close_archives_first_and_refuses_on_error(self):
        self.blade()
        with patch('blades.lifecycle.snapshot', side_effect=OSError('disk full')), patch('blades.tmuxctl.kill') as kill:
            response = self.client.delete('/api/blades/blade-demo-1', headers=self.headers)
        self.assertEqual(response.status_code, 409)
        kill.assert_not_called()
        self.assertNotEqual(self.store.get('blade-demo-1')['state'], 'closed')

    def test_resume_uses_exact_provider_and_session(self):
        b = self.blade(provider='codex')
        self.store.set_state(b['name'], 'closed', session_id='00000000-0000-0000-0000-000000000001')
        with patch.object(self.mod, '_open_blade', return_value=self.store.get(b['name'])) as launch, \
             patch('blades.lifecycle.restore_transcript'):
            response = self.client.post('/api/blades/'+b['name']+'/resume', json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(launch.call_args.kwargs['provider'], 'codex')
        self.assertEqual(launch.call_args.kwargs['resume_id'], '00000000-0000-0000-0000-000000000001')

    def test_concurrent_resume_opens_exactly_one_process(self):
        import threading
        b = self.blade(provider='codex')
        self.store.set_state(b['name'], 'closed', session_id='00000000-0000-0000-0000-000000000009')
        barrier = threading.Barrier(2)
        responses = []
        def binary(_):
            barrier.wait(timeout=4)
            return '/fake/codex'
        def resume():
            with self.mod.app.test_client() as client:
                responses.append(client.post('/api/blades/'+b['name']+'/resume', json={}, headers=self.headers).status_code)
        with patch('blades.projects.by_slug', return_value=self.project), \
             patch('blades.providers.executable', side_effect=binary), \
             patch('blades.lifecycle.restore_transcript'), \
             patch('blades.providers.transcript', return_value=None), \
             patch('blades.tmuxctl.list_sessions', return_value=[]), \
             patch('blades.tmuxctl.new_session') as launch:
            threads = [threading.Thread(target=resume) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=6)
            self.assertEqual(sorted(responses), [200, 409])
            self.assertEqual(launch.call_count, 1)

    def test_codex_child_completion_cannot_replace_main_identity(self):
        b = self.blade(provider='codex')
        sid = '00000000-0000-0000-0000-000000000001'
        self.store.mark_activity(b['name'], 'busy', 'human:input', session_id=sid)
        with patch('blades.providers.codex_completion_transcript', return_value=None):
            response = self.client.post('/hook', json={'blade': b['name'], 'event': 'CodexTurnComplete',
                'payload': {'session_id': '00000000-0000-0000-0000-000000000002'}})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['ignored'])
        self.assertEqual(self.store.get(b['name'])['session_id'], sid)
        self.assertEqual(self.store.get(b['name'])['state'], 'busy')

    def test_nightly_uses_inactivity_policy(self):
        with patch.object(self.mod, '_reconcile'), patch('blades.lifecycle.sweep', return_value=[]) as sweep, \
             patch('blades.winddown.start') as close:
            response = self.client.post('/api/maintenance/run', json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        sweep.assert_called_once()
        close.assert_not_called()

    def test_invalid_settings_and_resize_rejected(self):
        self.blade()
        for body in ({'idle_timeout_min': -1}, {'idle_timeout_min': True}, {'idle_grace_sec': 0}, {'tmux_cols': 999999}):
            self.assertEqual(self.client.put('/api/settings', json=body, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post('/api/blades/blade-demo-1/resize', json={'cols': 0}, headers=self.headers).status_code, 400)

    def test_busy_session_cannot_wind_down(self):
        self.blade()
        self.store.set_state('blade-demo-1', 'busy')
        with patch('blades.winddown.start') as start:
            response = self.client.post('/api/blades/blade-demo-1/winddown', json={}, headers=self.headers)
        self.assertEqual(response.status_code, 409)
        start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
