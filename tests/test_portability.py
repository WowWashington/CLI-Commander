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
        self.assertEqual(audit.project_roots(), [str(one.resolve())])
        server_config.configure({'project_roots': [str(one), str(two), str(one)]})
        self.assertEqual(audit.project_roots(), [str(one.resolve()), str(two.resolve())])

    def test_code_locations_legacy_default_and_override(self):
        from blades import locations
        home = self.root / 'home'
        (home / 'Projects').mkdir(parents=True)
        with patch.object(config, 'HOME', str(home)):
            self.assertEqual(locations.configured(), [str(home / 'Projects')])
            config.save_json(config.path('server.json'), {'port': 5090, 'projects_root': str(self.root)})
            self.assertEqual(locations.configured(), [str(self.root)])
            server_config.configure({'project_roots': []})
            self.assertEqual(locations.configured(), [])
            self.assertEqual(config.server_settings(), {'port': 5090, 'project_roots': []})
            with patch.dict(os.environ, {'BLADES_PROJECTS_ROOT': str(home)}):
                self.assertEqual(locations.configured(), [str(home.resolve())])
                with self.assertRaisesRegex(RuntimeError, 'BLADES_PROJECTS_ROOT'):
                    server_config.configure({'project_roots': [str(self.root)]})

    def test_invalid_code_locations_rejected_without_saving(self):
        for paths in (None, 'not-a-list', ['relative'], [str(self.root / 'missing')], ['/'], [str(self.root)] * 21, [3]):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                server_config.configure({'project_roots': paths})
        self.assertFalse((self.root / 'server.json').exists())

    def test_discovery_spans_locations_and_registers_only_their_children(self):
        from blades import audit, locations
        code, work = self.root / 'code', self.root / 'work'
        for folder in (code / 'alpha' / '.git', code / '.hidden', code / 'node_modules', work / 'beta', self.root / 'outside'):
            folder.mkdir(parents=True)
        server_config.configure({'project_roots': [str(code), str(work)]})
        found = {item['name']: item for item in audit.unregistered()}
        self.assertEqual(set(found), {'alpha', 'beta'})
        self.assertTrue(found['alpha']['is_git'])
        self.assertEqual(found['beta']['root'], str(work.resolve()))
        audit.register(path=found['beta']['path'])
        audit.register('alpha')
        self.assertEqual(audit.unregistered(), [])
        with self.assertRaises(RuntimeError):
            audit.register(path=str(self.root / 'outside'))
        preview = locations.preview([str(self.root)])
        self.assertIn('outside', [item['name'] for item in preview])
        self.assertNotIn('code', [item['name'] for item in preview if item['registered']])

    def test_removing_project_keeps_folder_and_rejects_legacy_index_rows(self):
        folder, legacy = self.root / 'keep me', self.root / 'legacy'
        folder.mkdir(); legacy.mkdir(); (folder / 'file.txt').write_text('data')
        Path(config.PROJECTS_MD).write_text(f"| 1 | Old | old | `{legacy}` | — | — | — | Active |\n")
        item = projects.add_project(str(folder))
        config.save_layout({'order': ['old', item['slug']], 'hidden': [item['slug']]})
        projects.remove_project(item['slug'])
        self.assertIsNone(projects.by_slug(item['slug']))
        self.assertEqual((folder / 'file.txt').read_text(), 'data')
        self.assertEqual(config.layout(), {'order': ['old'], 'hidden': []})
        with self.assertRaisesRegex(ValueError, 'projects.md'):
            projects.remove_project('old')
        with self.assertRaises(LookupError):
            projects.remove_project('missing')

    def test_case_variant_paths_are_the_same_folder(self):
        from blades import locations
        folder = self.root / 'Code'
        (folder / 'app').mkdir(parents=True)
        variant = self.root / 'code'
        if not variant.exists():
            self.skipTest('case-sensitive filesystem')
        projects.add_project(str(folder / 'app'))
        with self.assertRaisesRegex(ValueError, 'already registered'):
            projects.add_project(str(variant / 'app'))
        self.assertEqual(len(locations.validate([str(folder), str(variant)])), 1)
        self.assertEqual(locations.preview([str(variant)]), [])

    def test_instance_name_saved_cleared_and_overridden(self):
        self.assertEqual(config.instance_name(), '')
        self.assertEqual(server_config.configure({'instance_name': '  CLI Dev '})['instance_name'], 'CLI Dev')
        self.assertEqual(config.server_settings()['instance_name'], 'CLI Dev')
        with patch.dict(os.environ, {'BLADES_INSTANCE_NAME': 'CLI Prod'}):
            self.assertEqual(config.instance_name(), 'CLI Prod')
        for bad in ('x' * 41, 'bad\nname', 3):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                server_config.configure({'instance_name': bad})
        server_config.configure({'instance_name': ''})
        self.assertNotIn('instance_name', config.server_settings())

    def test_unmodified_legacy_prompt_follows_current_default(self):
        legacy = config.DEFAULT_PROMPT.replace("CLI Commander", "CLI Blades")
        config.save_json(config.path("defaults.json"), {"prompt_template": legacy, "idle_timeout_min": 60})
        self.assertEqual(config.settings()["prompt_template"], config.DEFAULT_PROMPT)
        self.assertEqual(config.load_json(config.path("defaults.json"), {}), {"idle_timeout_min": 60})
        custom = legacy + "\nMy own line"
        config.save_json(config.path("defaults.json"), {"prompt_template": custom})
        self.assertEqual(config.settings()["prompt_template"], custom)

    def test_create_project_makes_folder_state_and_registers(self):
        parent = self.root / "Projects"
        parent.mkdir()
        server_config.configure({"project_roots": [str(parent)]})
        item = projects.create_project("New: thing", git=True)
        folder = parent / "New- thing"
        self.assertEqual(item["path"], str(folder.resolve()))
        self.assertIn("# New: thing", (folder / "STATE.md").read_text())
        self.assertIn("created from CLI Commander", (folder / "STATE.md").read_text())
        self.assertTrue((folder / ".git").is_dir())
        self.assertEqual(projects.by_slug(item["slug"])["name"], "New: thing")
        with self.assertRaisesRegex(ValueError, "already exists"):
            projects.create_project("New: thing", str(parent))
        other = projects.create_project("Plain", str(parent), git=False)
        self.assertFalse((Path(other["path"]) / ".git").exists())

    def test_create_project_rejects_bad_input_and_cleans_up(self):
        parent = self.root / "Projects"
        parent.mkdir()
        for name in (None, "", "  ", ".hidden", "..", "x" * 121, "bad\x01"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                projects.create_project(name, str(parent))
        with self.assertRaises(ValueError):
            projects.create_project("Ok", str(self.root / "missing"))
        with self.assertRaises(ValueError):
            projects.create_project("Ok", "relative")
        with patch.object(projects, "add_project", side_effect=ValueError("boom")):
            with self.assertRaisesRegex(ValueError, "boom"):
                projects.create_project("Rollback", str(parent))
        self.assertFalse((parent / "Rollback").exists())

    def test_create_project_api(self):
        from blades.app import app
        parent = self.root / "Projects"
        parent.mkdir()
        server_config.configure({"project_roots": [str(parent)]})
        client = app.test_client()
        headers = {"Origin": "http://127.0.0.1:5090", "Host": "127.0.0.1:5090", "X-Blades-Request": "1"}
        r = client.post("/api/projects", json={"create": True, "name": "Via API", "git": False}, headers=headers)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue((parent / "Via API" / "STATE.md").exists())
        r = client.post("/api/projects", json={"create": True, "name": "Via API"}, headers=headers)
        self.assertEqual(r.status_code, 400)
