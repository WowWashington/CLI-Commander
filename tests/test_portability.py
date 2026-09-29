"""Fresh-install, private host settings and portable project registry contracts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from blades import config, projects, server_config, install


class PortabilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for key, value in {"CONFIG_DIR": str(self.root), "PROJECTS_MD": str(self.root / "no-index.md"), "PORT": 5090}.items():
            p = patch.object(config, key, value)
            p.start(); self.addCleanup(p.stop)
        env = patch.dict(os.environ, {}, clear=True)
        env.start(); self.addCleanup(env.stop)

    def test_fresh_project_registration_needs_no_personal_markdown(self):
        folder = self.root / "Work with spaces"
        folder.mkdir()
        item = projects.add_project(str(folder), "My work")
        self.assertEqual(projects.by_slug(item["slug"])["path"], str(folder.resolve()))
        self.assertFalse(Path(config.PROJECTS_MD).exists())
        self.assertEqual((self.root / "projects.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            projects.add_project(str(folder / ".." / folder.name))

    def test_existing_index_is_preserved_and_slugs_do_not_collide(self):
        one, two = self.root / "one", self.root / "two"
        one.mkdir(); two.mkdir()
        index = Path(config.PROJECTS_MD)
        original = f"| 1 | Demo | demo | `{one}` | — | — | — | Active |\n"
        index.write_text(original)
        item = projects.add_project(str(two), "Demo")
        self.assertEqual(item["slug"], "demo-2")
        self.assertEqual(index.read_text(), original)
        self.assertEqual(len(projects.parse_projects_md()), 2)

    def test_invalid_project_and_audit_traversal_rejected(self):
        from blades import audit
        for folder in (None, "", "relative", str(self.root / "missing")):
            with self.subTest(folder=folder), self.assertRaises(ValueError):
                projects.add_project(folder)
        for name in ("../escape", ".", "..", "/absolute"):
            with self.assertRaises(RuntimeError):
                audit.register(name)

    def test_port_change_checks_active_sessions_and_does_not_apply_live(self):
        with patch('blades.tmuxctl.list_sessions', return_value=['blade-demo-1']):
            with self.assertRaisesRegex(RuntimeError, 'Close live'):
                server_config.configure({"port": 5500})
        self.assertFalse((self.root / 'server.json').exists())
        with patch('blades.tmuxctl.list_sessions', return_value=[]), \
             patch.object(config, 'bind_addresses', return_value=['127.0.0.1']), \
             patch('blades.server_config.socket.socket'):
            result = server_config.configure({"port": 5500})
        self.assertEqual(config.PORT, 5090)
        self.assertEqual(result['configured_port'], 5500)
        self.assertTrue(result['restart_required'])
        self.assertEqual((self.root / 'server.json').stat().st_mode & 0o777, 0o600)

    def test_bad_ports_and_environment_override_cannot_silently_change(self):
        for value in (True, 80, 65536, 1.5, '5500'):
            with self.assertRaises(ValueError):
                server_config.configure({'port': value})
        with patch.dict(os.environ, {'BLADES_PORT': '5090'}), patch('blades.tmuxctl.list_sessions', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'overrides'):
                server_config.configure({'port': 5500})

    def test_clean_process_loads_saved_port_and_env_takes_precedence(self):
        config.save_json(config.path('server.json'), {'port': 5500})
        env = {'BLADES_HOME': str(self.root), 'HOME': str(self.root), 'PATH': os.defpath}
        script = 'from blades import config; print(config.PORT)'
        for override, expected in ((None, '5500'), ('5501', '5501')):
            if override:
                env['BLADES_PORT'] = override
            result = subprocess.run([sys.executable, '-c', script], env=env, text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), expected)

    def test_generated_service_does_not_freeze_port_or_local_user(self):
        plist = install._plist(install.SERVICE_LABEL, ['python', '-m', 'blades', 'serve'], {})
        self.assertNotIn('BLADES_PORT', plist['EnvironmentVariables'])
        self.assertEqual(plist['EnvironmentVariables']['BLADES_HOME'], config.CONFIG_DIR)
        self.assertEqual(plist['Label'], 'io.cli-blades.server')

    def test_paths_with_spaces_are_quoted_in_provider_hooks(self):
        import shlex
        with patch.object(install, 'HOOK', '/tmp/Shared Projects/cli/hooks/blade-hook.sh'):
            data = json.loads(Path(install.hooks_settings_path()).read_text())
        command = data['hooks']['Stop'][0]['hooks'][0]['command']
        self.assertEqual(shlex.split(command), ['/tmp/Shared Projects/cli/hooks/blade-hook.sh', 'Stop'])

    def test_service_preserves_account_directories_without_copying_api_keys(self):
        with patch.dict(os.environ, {'CODEX_HOME': '/tmp/accounts/codex', 'CLAUDE_CONFIG_DIR': '/tmp/accounts/claude',
                                     'GEMINI_CLI_HOME': '/tmp/accounts/google', 'OPENAI_API_KEY': 'dummy-test-only'}):
            env = install._plist(install.SERVICE_LABEL, [], {})['EnvironmentVariables']
        self.assertEqual(env['CODEX_HOME'], '/tmp/accounts/codex')
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], '/tmp/accounts/claude')
        self.assertEqual(env['GEMINI_CLI_HOME'], '/tmp/accounts/google')
        self.assertNotIn('OPENAI_API_KEY', env)

    def test_project_discovery_folder_updates_without_server_restart(self):
        from blades import audit
        one, two = self.root / 'one', self.root / 'two'
        one.mkdir(); two.mkdir()
        server_config.configure({'projects_root': str(one)})
        self.assertEqual(Path(audit.project_root()), one.resolve())
        server_config.configure({'projects_root': str(two)})
        self.assertEqual(Path(audit.project_root()), two.resolve())
