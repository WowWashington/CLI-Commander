"""Provider launch, exact resume lookup, and durable naming tests."""
import json
from contextlib import closing
import os
import shlex
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_IMPORT_HOME = tempfile.TemporaryDirectory(prefix="blades-provider-import-")
with mock.patch.dict(os.environ, {"BLADES_HOME": _IMPORT_HOME.name}):
    from blades import config, install, providers, store, tmuxctl


class IsolatedProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blades-provider-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "private state"
        (self.state / "run").mkdir(parents=True)
        self.codex_home = self.root / "codex"
        self.codex_home.mkdir()
        self.patches = [mock.patch.object(config, "CONFIG_DIR", str(self.state)),
                        mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}),
                        mock.patch.object(store, "_conn", None),
                        mock.patch.object(store, "_blade_locks", {})]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        if store._conn is not None:
            store._conn.close()
        for patch in reversed(self.patches):
            patch.stop()

    def test_archived_names_are_never_reused_or_overwritten(self):
        name = "blade-example-1"
        store.create(name, "example", "Example", str(self.root))
        store.set_state(name, "closed", note="retained conversation", session_id="exact-id")
        store.record_event(name, "Stop", {"saved": True})
        with mock.patch.object(tmuxctl, "list_sessions", return_value=["blade-example-2"]):
            self.assertEqual(tmuxctl.next_name("example"), "blade-example-3")
        with self.assertRaises(sqlite3.IntegrityError):
            store.create(name, "example", "Other", str(self.root), provider="codex")
        old = store.get(name)
        self.assertEqual(old["state"], "closed")
        self.assertEqual(old["session_id"], "exact-id")
        self.assertEqual(old["project"], "Example")
        self.assertEqual(store.last_event(name)["event"], "Stop")

    def test_codex_lookup_uses_exact_id_in_read_only_database(self):
        session_id = "11111111-2222-3333-4444-555555555555"
        expected = self.codex_home / "saved transcript.jsonl"
        expected.write_text("{}\n", encoding="utf-8")
        with closing(sqlite3.connect(self.codex_home / "state_5.sqlite")) as db:
            db.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT)")
            db.execute("INSERT INTO threads VALUES(?,?)", (session_id, str(expected)))
            db.commit()
        self.assertEqual(providers.transcript("codex", session_id), str(expected))
        self.assertIsNone(providers.transcript("codex", "missing-session"))
        self.assertIsNone(providers.transcript("codex", "' OR 1=1 --"))
        self.assertIsNone(providers.transcript("codex", None))

    def test_legacy_codex_lookup_covers_archived_history_without_latest_fallback(self):
        folder = self.codex_home / "archived_sessions" / "2026"
        folder.mkdir(parents=True)
        expected = folder / "rollout-2026-01-01-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.jsonl"
        expected.write_text("{}\n", encoding="utf-8")
        self.assertEqual(providers.transcript("codex", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), str(expected))
        self.assertIsNone(providers.transcript("codex", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
        for invalid in ("*", "aaaaaaaaaaaa", "?", "[a-z]*"):
            with self.subTest(session_id=invalid):
                self.assertIsNone(providers.transcript("codex", invalid))

    def test_missing_provider_fails_without_starting_tmux(self):
        with mock.patch.object(providers, "executable", return_value=None), \
                mock.patch.object(tmuxctl, "_run") as run:
            with self.assertRaisesRegex(RuntimeError, "not installed"):
                tmuxctl.new_session("blade-missing-1", str(self.root), "hello", provider="codex")
            run.assert_not_called()

    def run_fake_launch(self, provider, resume_id=None):
        name = "blade-example-1"
        # Both the executable and working directory require shell quoting.
        cwd = self.root / "project with spaces ' and $cash"
        cwd.mkdir()
        executable = self.root / "provider executable ' with spaces"
        output = self.root / "arguments.json"
        marker = self.root / "MUST-NOT-EXIST"
        capture_script = self.root / "capture provider.py"
        capture_script.write_text("import json, os, sys\n"
                              "from pathlib import Path\n"
                              "Path(os.environ['BLADES_TEST_CAPTURE']).write_text(json.dumps({"
                              "'argv':sys.argv[1:],'cwd':os.getcwd(),'blade':os.environ.get('BLADE_ID')}))\n",
                              encoding="utf-8")
        executable.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, str(capture_script)]) + ' "$@"\n')
        executable.chmod(0o700)
        prompt = "Keep this literal: $(touch " + str(marker) + "); `echo nope`\nsecond line ' \""
        hook_path = str(self.root / "hooks ' config.json")
        calls_before_ready = []

        def fake_tmux(args, **kwargs):
            calls_before_ready.append((args, (self.state / "run" / (name + ".ready")).exists()))
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch.object(providers, "executable", return_value=str(executable)), \
                mock.patch.object(install, "hooks_settings_path", return_value=hook_path), \
                mock.patch.object(tmuxctl, "_run", side_effect=fake_tmux):
            tmuxctl.new_session(name, str(cwd), prompt, provider=provider, resume_id=resume_id)
        script = self.state / "run" / (name + ".sh")
        env = dict(os.environ, BLADES_TEST_CAPTURE=str(output))
        subprocess.run(["/bin/zsh", "-f", str(script)], env=env, check=True, capture_output=True, text=True)
        captured = json.loads(output.read_text())
        self.assertEqual(Path(captured["cwd"]).resolve(), cwd.resolve())
        self.assertEqual(captured["blade"], name)
        self.assertEqual(captured["argv"][-1], prompt)
        self.assertFalse(marker.exists(), "prompt text executed as a shell command")
        self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.state / "run" / (name + ".prompt")).stat().st_mode), 0o600)
        self.assertTrue((self.state / "run" / (name + ".ready")).exists())
        self.assertTrue(all(not ready for _, ready in calls_before_ready))
        self.assertEqual(calls_before_ready[0][0][0], "new-session")
        self.assertIn(["set-option", "-t", name, "remain-on-exit", "on"],
                      [args for args, _ in calls_before_ready])
        return captured["argv"], hook_path

    def test_claude_launch_quotes_prompt_paths_and_exact_resume_id(self):
        sid = "session-id-with-safe-literal-characters"
        args, hook_path = self.run_fake_launch("claude", sid)
        self.assertEqual(args[:4], ["--settings", hook_path, "--resume", sid])
        self.assertNotIn("--continue", args)
        self.assertNotIn("--dangerously-skip-permissions", args)

    def test_codex_launch_preserves_approvals_and_provider_notifications(self):
        args, _ = self.run_fake_launch("codex", "exact-codex-id")
        self.assertEqual(args[:2], ["resume", "exact-codex-id"])
        self.assertIn("--no-alt-screen", args)
        self.assertEqual(args[args.index("--sandbox") + 1], "workspace-write")
        self.assertEqual(args[args.index("--ask-for-approval") + 1], "on-request")
        notify = args[args.index("-c") + 1]
        self.assertTrue(notify.startswith("notify="))
        self.assertEqual(json.loads(notify[len("notify="):]),
                         [sys.executable, str(Path(install.REPO) / "hooks" / "codex-notify.py")])
        self.assertNotIn("--last", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)

    def test_new_codex_session_does_not_accidentally_resume(self):
        args, _ = self.run_fake_launch("codex")
        self.assertNotIn("resume", args)
        self.assertNotIn("--last", args)


if __name__ == "__main__":
    unittest.main()
