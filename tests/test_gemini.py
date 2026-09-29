"""Private Gemini launch settings, exact provider identities, and safe callbacks."""
from contextlib import closing, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_IMPORT_HOME = tempfile.TemporaryDirectory(prefix="blades-gemini-import-")
with mock.patch.dict(os.environ, {"BLADES_HOME": _IMPORT_HOME.name}):
    from blades import config, install, providers, tmuxctl

SID = "11111111-2222-3333-4444-555555555555"
OTHER_SID = "11111111-2222-3333-4444-666666666666"


class GeminiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blades-gemini-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "private settings"
        (self.state / "run").mkdir(parents=True)
        self.admin = self.root / "admin"
        self.admin.mkdir()
        self.system = self.admin / "settings.json"
        self.defaults = self.admin / "system-defaults.json"
        self.patches = [mock.patch.object(config, "CONFIG_DIR", str(self.state)),
                        mock.patch.dict(os.environ, {
                            "GEMINI_CLI_SYSTEM_SETTINGS_PATH": str(self.system),
                            "GEMINI_CLI_SYSTEM_DEFAULTS_PATH": str(self.defaults),
                            "GEMINI_CLI_HOME": str(self.root / "gemini home"),
                            "CODEX_HOME": str(self.root / "codex"),
                        })]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_overlay_preserves_admin_settings_hooks_defaults_and_policies(self):
        original = {"security": {"auth": {"enforcedType": "oauth-personal"}},
                    "tools": {"exclude": ["run_shell_command"], "enableHooks": False},
                    "hooks": {"AfterAgent": [{"hooks": [{"type": "command", "command": "admin-hook"}]}]}}
        self.system.write_text(json.dumps(original))
        self.defaults.write_text('{"model":{"name":"chosen"}}')
        policies = self.admin / "policies"
        policies.mkdir()
        (policies / "admin.toml").write_text('rule = "keep"')
        env = install.gemini_launch_environment("blade-example-1")
        generated = Path(env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"])
        data = json.loads(generated.read_text())
        self.assertEqual(data["security"], original["security"])
        self.assertEqual(data["tools"], original["tools"])
        self.assertEqual(data["hooks"]["AfterAgent"][0], original["hooks"]["AfterAgent"][0])
        self.assertEqual(len(data["hooks"]["AfterAgent"]), 2)
        self.assertEqual(set(data["hooks"]), set(install.GEMINI_EVENTS))
        self.assertEqual(json.loads(self.system.read_text()), original)
        self.assertEqual(self.defaults.read_text(), '{"model":{"name":"chosen"}}')
        self.assertEqual(env["GEMINI_CLI_SYSTEM_DEFAULTS_PATH"], str(self.defaults))
        self.assertEqual((generated.parent / "policies").resolve(), policies.resolve())
        self.assertEqual(stat.S_IMODE(generated.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(generated.parent.stat().st_mode), 0o700)
        self.assertNotIn("GEMINI_CLI_HOME", env)
        self.assertNotIn("HOME", env)
        install.gemini_launch_environment("blade-example-1")
        self.assertEqual(len(json.loads(generated.read_text())["hooks"]["AfterAgent"]), 2)

    def test_invalid_admin_settings_fail_before_launch_without_overwriting(self):
        self.system.write_text('{"security": broken')
        with mock.patch.object(providers, "executable", return_value="gemini"), \
                mock.patch.object(tmuxctl, "_run") as run:
            with self.assertRaisesRegex(RuntimeError, "preserve Gemini system settings"):
                tmuxctl.new_session("blade-example-1", str(self.root), "hello", provider="gemini")
        run.assert_not_called()
        self.assertEqual(self.system.read_text(), '{"security": broken')

    def test_hook_overlay_never_replaces_an_unexpected_policy_path(self):
        folder = self.state / "run" / "blade-example-1.gemini"
        folder.mkdir()
        (folder / "policies").mkdir()
        with self.assertRaisesRegex(RuntimeError, "original policies"):
            install.gemini_launch_environment("blade-example-1")
        self.assertTrue((folder / "policies").is_dir())

    def fake_launch(self, prompt, resume_id=None):
        executable = self.root / "fake Gemini ' executable"
        output = self.root / "captured.json"
        capture_script = self.root / "capture Gemini.py"
        capture_script.write_text("import json,os,sys\n"
                              "from pathlib import Path\n"
                              "Path(os.environ['BLADES_TEST_CAPTURE']).write_text(json.dumps({"
                              "'args':sys.argv[1:],'cwd':os.getcwd(),"
                              "'settings':os.environ['GEMINI_CLI_SYSTEM_SETTINGS_PATH'],"
                              "'home':os.environ['GEMINI_CLI_HOME']}))\n")
        executable.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, str(capture_script)]) + ' "$@"\n')
        executable.chmod(0o700)
        cwd = self.root / "project ' with spaces $cash"
        cwd.mkdir()
        with mock.patch.object(providers, "executable", return_value=str(executable)), \
                mock.patch.object(tmuxctl, "_run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            tmuxctl.new_session("blade-example-1", str(cwd), prompt, resume_id=resume_id, provider="gemini")
        subprocess.run(["/bin/zsh", "-f", str(self.state / "run" / "blade-example-1.sh")],
                       env=dict(os.environ, BLADES_TEST_CAPTURE=str(output)), check=True, capture_output=True)
        return json.loads(output.read_text()), cwd

    def test_prompt_is_literal_and_stays_interactive_with_normal_approvals(self):
        marker = self.root / "MUST-NOT-EXIST"
        prompt = "Literal $(touch " + str(marker) + "); `echo nope`\n'quote'"
        data, cwd = self.fake_launch(prompt)
        self.assertEqual(data["args"], ["--approval-mode", "default", "--prompt-interactive", prompt])
        self.assertEqual(Path(data["cwd"]).resolve(), cwd.resolve())
        self.assertEqual(data["home"], os.environ["GEMINI_CLI_HOME"])
        self.assertFalse(marker.exists())
        self.assertTrue(Path(data["settings"]).is_file())

    def test_resume_uses_exact_id_and_does_not_replay_the_initial_prompt(self):
        data, _ = self.fake_launch("", SID)
        self.assertEqual(data["args"], ["--approval-mode", "default", "--resume", SID])
        self.assertNotIn("latest", data["args"])

    def test_gemini_lookup_verifies_full_id_and_project_not_filename_prefix(self):
        project_hash = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()
        chats = Path(os.environ["GEMINI_CLI_HOME"]) / ".gemini" / "tmp" / project_hash / "chats"
        chats.mkdir(parents=True)
        path = chats / ("session-2026-09-29-" + SID[:8] + ".json")
        path.write_text(json.dumps({"sessionId": OTHER_SID, "projectHash": project_hash, "messages": []}))
        self.assertIsNone(providers.transcript("gemini", SID))
        path.write_text(json.dumps({"sessionId": SID, "projectHash": project_hash, "messages": []}))
        self.assertEqual(providers.transcript("gemini", SID, str(self.root)), str(path))
        self.assertIsNone(providers.transcript("gemini", SID, str(self.root / "other")))
        self.assertIsNone(providers.transcript("gemini", "*"))

    def test_claude_lookup_recovers_only_exact_native_transcript(self):
        folder = self.root / ".claude" / "projects" / "project"
        folder.mkdir(parents=True)
        path = folder / (SID + ".jsonl")
        path.write_text("{}\n")
        with mock.patch.object(Path, "home", return_value=self.root):
            self.assertEqual(providers.transcript("claude", SID), str(path))
            self.assertIsNone(providers.transcript("claude", OTHER_SID))

    def test_relay_sends_only_metadata_to_loopback_and_returns_no_decision(self):
        spec = importlib.util.spec_from_file_location("gemini_hook_test", Path(install.REPO) / "hooks" / "gemini-hook.py")
        relay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(relay)
        native = {"session_id": SID, "hook_event_name": "AfterAgent", "cwd": str(self.root),
                  "transcript_path": "/expected/session.json", "stop_hook_active": False,
                  "prompt": "PRIVATE PROMPT", "prompt_response": "PRIVATE RESPONSE",
                  "tool_input": {"command": "PRIVATE COMMAND", "run_in_background": True}}
        opener = mock.Mock()
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["gemini-hook.py", "AfterAgent"]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(native))), \
                mock.patch.dict(os.environ, {"BLADE_ID": "blade-example-1", "BLADES_PORT": "12345"}), \
                mock.patch.object(relay.urllib.request, "build_opener", return_value=opener), redirect_stdout(output):
            relay.main()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:12345/hook")
        payload = json.loads(request.data)
        self.assertEqual(payload["event"], "GeminiAfterAgent")
        self.assertEqual(payload["payload"]["session_id"], SID)
        self.assertEqual(payload["payload"]["tool_input"], {"run_in_background": True})
        self.assertNotIn("PRIVATE", request.data.decode())
        self.assertEqual(json.loads(output.getvalue()), {})

    def test_codex_completion_rejects_child_or_wrong_directory_even_with_rollout(self):
        home = Path(os.environ["CODEX_HOME"])
        home.mkdir()
        rollout = home / "rollout.jsonl"
        rollout.write_text("{}\n")
        db_path = home / "state_5.sqlite"
        with closing(sqlite3.connect(db_path)) as db:
            db.execute("CREATE TABLE threads(id TEXT, cwd TEXT, source TEXT, thread_source TEXT, agent_path TEXT, agent_role TEXT, rollout_path TEXT)")
            db.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?)", (SID, str(self.root), "cli", "user", None, None, str(rollout)))
            db.commit()
        self.assertEqual(providers.codex_completion_transcript(SID, str(self.root)), str(rollout))
        self.assertIsNone(providers.codex_completion_transcript(SID, str(self.root / "other")))
        self.assertIsNone(providers.codex_completion_transcript(OTHER_SID, str(self.root)))
        with closing(sqlite3.connect(db_path)) as db:
            db.execute("UPDATE threads SET agent_path='root/helper'")
            db.commit()
        self.assertIsNone(providers.codex_completion_transcript(SID, str(self.root)))

    def test_codex_legacy_completion_requires_top_level_session_metadata(self):
        folder = Path(os.environ["CODEX_HOME"]) / "sessions"
        folder.mkdir(parents=True)
        path = folder / ("rollout-" + SID + ".jsonl")
        metadata = {"id": SID, "cwd": str(self.root), "source": {"subagent": "review"}}
        path.write_text(json.dumps({"type": "session_meta", "payload": metadata}) + "\n")
        self.assertIsNone(providers.codex_completion_transcript(SID, str(self.root)))
        metadata["source"] = "cli"
        path.write_text(json.dumps({"type": "session_meta", "payload": metadata}) + "\n")
        self.assertEqual(providers.codex_completion_transcript(SID, str(self.root)), str(path))

    def test_installed_gemini_contract_merges_all_hook_scopes_and_exact_resume(self):
        binary = shutil.which("gemini")
        node = shutil.which("node")
        if not binary or not node:
            self.skipTest("installed Gemini/Node unavailable; unit launch checks still apply")
        resolved = Path(binary).resolve()
        package = next((parent for parent in resolved.parents
                        if (parent / "dist/src/config/settings.js").is_file()), None)
        if package is None:
            self.skipTest("installed Gemini JavaScript source not available")
        core = package / "node_modules/@google/gemini-cli-core/dist/src"
        if not (core / "config/storage.js").is_file():
            self.skipTest("installed Gemini core source not available")
        environment = install.gemini_launch_environment("blade-example-1")
        # Execute pure exported helpers from the installed official package with
        # an isolated home. No Gemini client, login or API request is started.
        script = "\n".join([
            "import assert from 'node:assert/strict';",
            "import {mergeSettings,getSystemDefaultsPath} from " + json.dumps((package / "dist/src/config/settings.js").as_uri()) + ";",
            "import {SessionSelector} from " + json.dumps((package / "dist/src/utils/sessionUtils.js").as_uri()) + ";",
            "import {Storage} from " + json.dumps((core / "config/storage.js").as_uri()) + ";",
            "import {HookEventName} from " + json.dumps((core / "hooks/types.js").as_uri()) + ";",
            "const hook = name => ({hooks:{AfterAgent:[{hooks:[{type:'command',command:name}]}]}});",
            "const merged=mergeSettings(hook('system'),hook('defaults'),hook('user'),hook('workspace'),true);",
            "assert.deepEqual(merged.hooks.AfterAgent.map(x=>x.hooks[0].command),['defaults','user','workspace','system']);",
            "assert.equal(getSystemDefaultsPath(),process.env.GEMINI_CLI_SYSTEM_DEFAULTS_PATH);",
            "assert.equal(Storage.getSystemPoliciesDir(),process.env.GEMINI_CLI_SYSTEM_SETTINGS_PATH.replace(/settings.json$/, 'policies'));",
            "for (const event of " + json.dumps(install.GEMINI_EVENTS) + ") assert.ok(Object.values(HookEventName).includes(event));",
            "const selector = new SessionSelector({});",
            "selector.listSessions=async()=>[{id:" + json.dumps(SID) + ",startTime:'2026-01-01'},{id:" + json.dumps(OTHER_SID) + ",startTime:'2026-01-02'}];",
            "assert.equal((await selector.findSession(" + json.dumps(SID) + ")).id," + json.dumps(SID) + ");",
            "console.log('installed Gemini contracts verified');",
        ])
        run = subprocess.run([node, "--input-type=module", "-e", script],
                             env=dict(os.environ, **environment, HOME=str(self.root)),
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("contracts verified", run.stdout)


if __name__ == "__main__":
    unittest.main()
