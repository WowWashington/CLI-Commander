"""Loss-resistant lifecycle checks. All storage and provider data are temporary."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from blades import config, lifecycle, prompts, store, winddown


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.patches = [patch.object(config, "CONFIG_DIR", str(self.base / "blades")),
                        patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.base / "claude"),
                                                "GEMINI_CLI_HOME": str(self.base / "geminihome")})]
        for p in self.patches:
            p.start()
        for sub in ("projects", "handoff/archive", "run"):
            Path(config.path(sub)).mkdir(parents=True, exist_ok=True)
        if store._conn is not None:
            store._conn.close()
        store._conn = None
        lifecycle._viewers.clear()
        # Lifecycle scenarios use an explicit one-hour fixture, independent of
        # the user's configured/default inactivity policy.
        config.save_settings({"idle_timeout_min": 60, "idle_grace_sec": 120})
        self.project = self.base / "project"
        self.project.mkdir()
        self.name = "blade-test-1"

    def tearDown(self):
        if store._conn is not None:
            store._conn.close()
            store._conn = None
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def blade(self, provider="claude", idle=True):
        b = store.create(self.name, "test", "Test", str(self.project), provider)
        if idle:
            source = {"codex": "codex:turn-complete", "gemini": "gemini:AfterAgent"}.get(provider, "claude:Stop")
            store.mark_activity(self.name, "idle", source, ts=time.time() - 7200)
            store.update(self.name, last_input_at=time.time() - 7200)
        return store.get(self.name)

    def transcript(self):
        source = self.base / "claude/projects/project/session-id.jsonl"
        source.parent.mkdir(parents=True)
        source.write_text('{"message":"the entire conversation"}\n')
        store.update(self.name, transcript_path=str(source), session_id="session-id")
        return source

    def gemini_payload(self):
        sid = "12345678-1234-4567-8123-123456789abc"
        project_hash = hashlib.sha256(str(self.project.resolve()).encode()).hexdigest()
        transcript = self.base / "geminihome/.gemini/tmp" / project_hash / "chats/session-2026-09-29-12345678.json"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(json.dumps({"sessionId": sid, "projectHash": project_hash,
                                          "messages": [{"type": "user", "content": "Keep the entire conversation"}]}))
        return {"session_id": sid, "cwd": str(self.project), "transcript_path": str(transcript)}

    def test_claude_opening_prompt_branding_preserves_legacy_and_user_notes(self):
        self.blade(idle=False)
        for prompt, expected in (
            (config.DEFAULT_PROMPT, ""),
            ('You are starting a fresh CLI Blades session for the project "Test".', ""),
            ("Review the UI\nKeep existing edits", "Review the UI"),
        ):
            with self.subTest(prompt=prompt.splitlines()[0]):
                store.apply_hook(self.name, "UserPromptSubmit", {"prompt": prompt})
                self.assertEqual(store.get(self.name)["note"], expected)

    def test_gemini_opening_prompt_branding_preserves_legacy_and_user_notes(self):
        self.blade(provider="gemini", idle=False)
        payload = self.gemini_payload()
        for prompt, expected in (
            (config.DEFAULT_PROMPT, ""),
            ('You are starting a fresh CLI Blades session for the project "Test".', ""),
            ("Review the UI\nKeep existing edits", "Review the UI"),
        ):
            with self.subTest(prompt=prompt.splitlines()[0]):
                store.apply_gemini_hook(self.name, "BeforeAgent", dict(payload, prompt=prompt))
                self.assertEqual(store.get(self.name)["note"], expected)

    def test_gemini_activity_requires_exact_hook_identity_and_explicit_completion(self):
        self.blade(provider="gemini", idle=False)
        payload = self.gemini_payload()
        store.apply_gemini_hook(self.name, "GeminiSessionStart", payload)
        self.assertFalse(lifecycle.proven_idle(store.get(self.name)))
        store.apply_gemini_hook(self.name, "GeminiBeforeAgent", payload)
        self.assertEqual(store.get(self.name)["state"], "busy")
        store.apply_gemini_hook(self.name, "GeminiAfterAgent", payload)
        self.assertTrue(lifecycle.proven_idle(store.get(self.name)))
        store.apply_gemini_hook(self.name, "GeminiBeforeModel", payload)
        self.assertFalse(lifecycle.proven_idle(store.get(self.name)))
        store.apply_gemini_hook(self.name, "GeminiAfterAgent", dict(payload, stop_hook_active=True))
        self.assertFalse(lifecycle.proven_idle(store.get(self.name)))
        store.apply_gemini_hook(self.name, "GeminiAfterAgent", payload)
        store.apply_gemini_hook(self.name, "GeminiNotification", dict(payload, message="Approve tool"))
        self.assertEqual(store.get(self.name)["state"], "attention")
        store.apply_gemini_hook(self.name, "GeminiSessionEnd", dict(payload, reason="clear"))
        self.assertNotEqual(store.get(self.name)["state"], "closed")

    def test_gemini_predicted_startup_path_does_not_block_manual_auth_screen_close(self):
        self.blade(provider="gemini", idle=False)
        payload = self.gemini_payload()
        Path(payload["transcript_path"]).unlink()
        store.apply_gemini_hook(self.name, "GeminiSessionStart", payload)
        self.assertIsNone(store.get(self.name)["transcript_path"])
        self.assertFalse(lifecycle.proven_idle(store.get(self.name)))
        with patch.object(lifecycle.tmuxctl, "capture_all", return_value="Sign in to Gemini"):
            lifecycle.snapshot(self.name, reason="manual-force-close")
        archive = lifecycle.archive_read(self.name)
        self.assertFalse(archive["transcript_available"])
        self.assertIn("Sign in to Gemini", archive["screen"])
        store.apply_gemini_hook(self.name, "GeminiAfterAgent", payload)
        self.assertFalse(lifecycle.proven_idle(store.get(self.name)))
        self.assertIsNone(store.get(self.name)["transcript_path"])
        self.assertEqual(store.get(self.name)["state"], "attention")

    def test_gemini_missing_previously_known_transcript_still_blocks_close(self):
        self.blade(provider="gemini", idle=False)
        payload = self.gemini_payload()
        store.apply_gemini_hook(self.name, "GeminiSessionStart", payload)
        Path(payload["transcript_path"]).unlink()
        store.apply_gemini_hook(self.name, "GeminiSessionStart", payload)
        self.assertEqual(store.get(self.name)["transcript_path"], payload["transcript_path"])
        store.apply_gemini_hook(self.name, "GeminiAfterAgent", payload)
        self.assertFalse(lifecycle.proven_idle(store.get(self.name)))
        with patch.object(lifecycle.tmuxctl, "capture_all", return_value="History existed earlier"):
            with self.assertRaises(FileNotFoundError):
                lifecycle.snapshot(self.name, reason="manual-force-close")

    def test_gemini_rejects_wrong_provider_conversation_project_and_transcript(self):
        self.blade(provider="gemini", idle=False)
        payload = self.gemini_payload()
        store.apply_gemini_hook(self.name, "GeminiSessionStart", payload)
        for invalid in (dict(payload, session_id="87654321-1234-4567-8123-123456789abc"),
                        dict(payload, cwd=str(self.base)),
                        dict(payload, transcript_path=str(self.base / "private.json"))):
            with self.assertRaises(RuntimeError):
                store.apply_gemini_hook(self.name, "GeminiAfterAgent", invalid)
            self.assertFalse(lifecycle.proven_idle(store.get(self.name)))
        source = Path(payload["transcript_path"])
        source.write_text(json.dumps({"sessionId": "other", "projectHash": "other"}))
        with self.assertRaisesRegex(RuntimeError, "conversation ID"):
            store.apply_gemini_hook(self.name, "GeminiAfterAgent", payload)
        source.write_text(json.dumps({"sessionId": payload["session_id"], "projectHash": "wrong-project"}))
        with self.assertRaisesRegex(RuntimeError, "project"):
            store.apply_gemini_hook(self.name, "GeminiAfterAgent", payload)
        store.update(self.name, provider="claude")
        with self.assertRaisesRegex(RuntimeError, "provider"):
            store.apply_gemini_hook(self.name, "GeminiAfterAgent", payload)

    def test_gemini_json_archive_and_restore_preserve_identity(self):
        self.blade(provider="gemini", idle=False)
        payload = self.gemini_payload()
        store.apply_gemini_hook(self.name, "GeminiAfterAgent", payload)
        with patch.object(lifecycle.tmuxctl, "capture_all", return_value="Gemini output"):
            archive = lifecycle.snapshot(self.name)
        self.assertTrue(Path(archive, "transcript.json").is_file())
        self.assertFalse(Path(archive, "transcript.jsonl").exists())
        self.assertTrue(lifecycle.archive_read(self.name)["transcript_available"])
        source = Path(payload["transcript_path"])
        source.unlink()
        self.assertEqual(lifecycle.restore_transcript(self.name), str(source))
        self.assertEqual(json.loads(source.read_text())["sessionId"], payload["session_id"])
        self.assertIn("entire conversation", source.read_text())
        source.write_text(json.dumps({"sessionId": "somebody-else", "projectHash": "wrong"}))
        with self.assertRaisesRegex(RuntimeError, "conversation ID"):
            lifecycle.restore_transcript(self.name)
        self.assertIn("somebody-else", source.read_text())

    def test_gemini_graceful_close_archives_json_before_quit(self):
        self.blade(provider="gemini", idle=False)
        payload = self.gemini_payload()
        store.apply_gemini_hook(self.name, "GeminiAfterAgent", payload)
        def send(name, text):
            self.assertEqual(text, "/quit")
            self.assertTrue(Path(store.get(name)["archive_path"], "transcript.json").is_file())
        with patch.object(winddown.tmuxctl, "capture_all", return_value="Gemini output"), \
             patch.object(winddown.tmuxctl, "send_text", side_effect=send), \
             patch.object(winddown, "_wait_for_end", return_value=True):
            self.assertEqual(winddown.run(self.name), "closed")

    def test_schema_migration_preserves_existing_row(self):
        db = sqlite3.connect(config.path("blades.db"))
        db.executescript(store.SCHEMA)
        db.execute("INSERT INTO blades(name,slug,project,cwd,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                   (self.name, "test", "Test", str(self.project), "idle", 1, 2))
        db.commit()
        db.close()
        row = store.get(self.name)
        self.assertEqual(row["provider"], "claude")
        self.assertIsNone(row["idle_since"])
        self.assertEqual(row["last_input_at"], 1)
        self.assertFalse(lifecycle.proven_idle(row))

    def test_duplicate_names_cannot_replace_history(self):
        self.blade()
        store.set_state(self.name, "closed", note="preserved history")
        with self.assertRaises(sqlite3.IntegrityError):
            self.blade()
        self.assertEqual(store.get(self.name)["note"], "preserved history")
        self.assertEqual(len(store.recent_closed()), 1)

    def test_startup_silence_and_attention_are_not_idle_evidence(self):
        self.blade(idle=False)
        store.apply_hook(self.name, "SessionStart", {"session_id": "test"})
        self.assertFalse(lifecycle.proven_idle(store.get(self.name)))
        store.apply_hook(self.name, "Stop", {})
        self.assertTrue(lifecycle.proven_idle(store.get(self.name)))
        store.apply_hook(self.name, "Notification", {"notification_type": "permission_prompt"})
        self.assertEqual(store.get(self.name)["state"], "attention")
        self.assertFalse(lifecycle.proven_idle(store.get(self.name)))

    def test_main_stop_does_not_close_running_background_subagent(self):
        self.blade()
        store.apply_hook(self.name, "SubagentStart", {"agent_id": "child-1"})
        store.apply_hook(self.name, "Stop", {})
        self.assertFalse(lifecycle.proven_idle(store.get(self.name)))
        store.apply_hook(self.name, "SubagentStop", {"agent_id": "child-1"})
        self.assertFalse(lifecycle.proven_idle(store.get(self.name)))
        store.apply_hook(self.name, "Stop", {})
        self.assertTrue(lifecycle.proven_idle(store.get(self.name)))

    def test_untracked_subagent_stop_keeps_idle_and_attention(self):
        # Observed: internal helpers send SubagentStop without SubagentStart after Stop.
        self.blade(idle=False)
        store.apply_hook(self.name, "UserPromptSubmit", {"prompt": "do it"})
        store.apply_hook(self.name, "Stop", {})
        store.apply_hook(self.name, "SubagentStop", {"agent_id": "a14cc9d5303aa5244", "agent_type": ""})
        self.assertEqual(store.get(self.name)["state"], "idle")
        self.assertTrue(lifecycle.proven_idle(store.get(self.name)))
        store.apply_hook(self.name, "Notification", {"notification_type": "permission_prompt", "message": "Claude needs your permission"})
        store.apply_hook(self.name, "SubagentStop", {"agent_id": "a783add47eb34b391", "agent_type": ""})
        self.assertEqual(store.get(self.name)["state"], "attention")
        self.assertEqual(store.get(self.name)["note"], "Claude needs your permission")

    def test_explicit_background_tool_blocks_main_stop_until_new_session_start(self):
        self.blade()
        payload = {"tool_name": "Bash", "tool_use_id": "tool-1",
                   "tool_input": {"command": "long-job", "run_in_background": True}}
        store.apply_hook(self.name, "PreToolUse", payload)
        store.apply_hook(self.name, "PostToolUse", payload)
        store.apply_hook(self.name, "Stop", {})
        self.assertFalse(lifecycle.proven_idle(store.get(self.name)))
        self.assertIn("tool:tool-1", json.loads(store.get(self.name)["active_subagents"]))
        self.assertIn("Background tool", store.get(self.name)["note"])
        store.apply_hook(self.name, "PostToolUse", {"tool_name": "TaskStop", "tool_input": {"task_id": "unknown"}})
        store.apply_hook(self.name, "Stop", {})
        self.assertFalse(lifecycle.proven_idle(store.get(self.name)))
        store.apply_hook(self.name, "SessionStart", {})
        self.assertFalse(lifecycle.proven_idle(store.get(self.name)))
        store.apply_hook(self.name, "Stop", {})
        self.assertTrue(lifecycle.proven_idle(store.get(self.name)))

    def test_continuing_stop_hook_is_not_completion(self):
        self.blade()
        store.apply_hook(self.name, "Stop", {"stop_hook_active": True})
        self.assertFalse(lifecycle.proven_idle(store.get(self.name)))

    def test_idle_notification_keeps_existing_completion_evidence(self):
        self.blade()
        before = store.get(self.name)["idle_since"]
        store.apply_hook(self.name, "Notification", {"notification_type": "idle_prompt"})
        self.assertEqual(store.get(self.name)["idle_since"], before)

    def test_input_cancels_idle_countdown_and_invalidates_proof(self):
        self.blade()
        store.update(self.name, idle_deadline=time.time() + 120)
        with lifecycle.interaction(self.name):
            pass
        b = store.get(self.name)
        self.assertIsNone(b["idle_deadline"])
        self.assertIsNone(b["idle_since"])
        self.assertEqual(b["state"], "unknown")
        self.assertGreater(b["last_input_at"], time.time() - 2)

    def test_viewers_do_not_keep_sessions_alive(self):
        b = self.blade()
        lifecycle.attach(self.name)
        self.assertEqual(lifecycle.idle_status(b)["viewers"], 1)
        self.assertEqual(store.get(self.name)["last_input_at"], b["last_input_at"])
        lifecycle.detach(self.name)
        self.assertEqual(lifecycle.idle_status(b)["viewers"], 0)

    def test_keepalive_extends_timer_without_inventing_completion(self):
        self.blade()
        lifecycle.keepalive(self.name)
        self.assertTrue(lifecycle.proven_idle(store.get(self.name)))
        self.assertEqual(lifecycle.sweep(), [])

    def test_sweep_grace_then_closes_only_proven_idle(self):
        self.blade()
        now = time.time()
        with patch.object(winddown, "start", return_value=True) as start:
            lifecycle.sweep(now)
            self.assertEqual(store.get(self.name)["idle_deadline"], now + 120)
            start.assert_not_called()
            lifecycle.sweep(now + 119)
            start.assert_not_called()
            self.assertEqual(lifecycle.sweep(now + 120), [self.name])
            start.assert_called_once_with(self.name, None, automatic=True)

    def test_sweep_never_closes_unknown_busy_attention_or_shell(self):
        self.blade()
        for state in ("unknown", "busy", "attention", "starting"):
            store.set_state(self.name, state)
            with patch.object(winddown, "start") as start:
                lifecycle.sweep(time.time() + 10000)
                start.assert_not_called()
        store.mark_activity(self.name, "idle", "claude:Stop", ts=1)
        store.update(self.name, provider="shell")
        with patch.object(winddown, "start") as start:
            lifecycle.sweep(time.time() + 10000)
            start.assert_not_called()

    def test_protection_and_disabled_timer_cancel_countdown(self):
        self.blade()
        store.update(self.name, idle_deadline=1)
        with patch.object(config, "protected", return_value={self.name}):
            lifecycle.sweep()
        self.assertIsNone(store.get(self.name)["idle_deadline"])
        store.update(self.name, idle_deadline=1)
        with patch.object(config, "settings", return_value={"idle_timeout_min": 0}):
            lifecycle.sweep()
        self.assertIsNone(store.get(self.name)["idle_deadline"])

    def test_archive_preserves_full_screen_transcript_and_recovery_files(self):
        self.blade()
        transcript = self.transcript()
        session_dir = transcript.parent / "session-id/subagents"
        session_dir.mkdir(parents=True)
        (session_dir / "child.jsonl").write_text("child conversation")
        file_history = self.base / "claude/file-history/session-id"
        file_history.mkdir(parents=True)
        (file_history / "original.txt").write_text("before edit")
        screen = "start of retained history\n" + ("very long conversation\n" * 2000)
        with patch.object(lifecycle.tmuxctl, "capture_all", return_value=screen):
            first = lifecycle.snapshot(self.name)
            second = lifecycle.snapshot(self.name)
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first, "terminal.txt").read_text(), screen)
        self.assertEqual(Path(first, "transcript.jsonl").read_text(), transcript.read_text())
        self.assertEqual(Path(first, "session-files/subagents/child.jsonl").read_text(), "child conversation")
        self.assertEqual(Path(first, "file-history/original.txt").read_text(), "before edit")
        self.assertTrue(lifecycle.archive_read(self.name)["transcript_available"])
        self.assertEqual(Path(first, "terminal.txt").stat().st_mode & 0o777, 0o600)
        self.assertTrue(lifecycle.snapshot_unchanged(second))
        transcript.write_text("new activity")
        self.assertFalse(lifecycle.snapshot_unchanged(second))

    def test_restore_claude_after_retention_purge_without_overwriting_newer_files(self):
        self.blade()
        transcript = self.transcript()
        child = transcript.parent / "session-id/subagents/child.jsonl"
        child.parent.mkdir(parents=True)
        child.write_text("subagent conversation")
        original = self.base / "claude/file-history/session-id/original.txt"
        original.parent.mkdir(parents=True)
        original.write_text("old contents")
        with patch.object(lifecycle.tmuxctl, "capture_all", return_value="screen"):
            lifecycle.snapshot(self.name)
        transcript.unlink()
        shutil.rmtree(child.parent.parent)
        self.assertEqual(lifecycle.restore_transcript(self.name), str(transcript))
        self.assertIn("the entire conversation", transcript.read_text())
        self.assertEqual(child.read_text(), "subagent conversation")
        self.assertEqual(original.read_text(), "old contents")
        transcript.write_text("newer conversation")
        original.write_text("newer file history")
        lifecycle.restore_transcript(self.name)
        self.assertEqual(transcript.read_text(), "newer conversation")
        self.assertEqual(original.read_text(), "newer file history")

    def test_restore_rejects_terminal_only_archive(self):
        self.blade()
        with patch.object(lifecycle.tmuxctl, "capture_all", return_value="screen"):
            lifecycle.snapshot(self.name)
        with self.assertRaisesRegex(RuntimeError, "terminal history only"):
            lifecycle.restore_transcript(self.name)

    def test_dead_pane_confirms_native_exit_without_session_end_hook(self):
        self.blade(provider="codex")
        with patch.object(winddown.tmuxctl, "exists", return_value=True), \
             patch.object(winddown.tmuxctl, "pane_dead", return_value=True):
            self.assertTrue(winddown._wait_for_end(self.name, 1))

    def test_untrusted_transcript_path_cannot_archive_arbitrary_files(self):
        self.blade()
        arbitrary = self.base / "private.jsonl"
        arbitrary.write_text("private")
        store.update(self.name, transcript_path=str(arbitrary))
        with patch.object(lifecycle.tmuxctl, "capture_all", return_value="screen"):
            with self.assertRaisesRegex(RuntimeError, "outside"):
                lifecycle.snapshot(self.name)
        self.assertIsNone(store.get(self.name)["archive_path"])

    def test_force_snapshot_without_transcript_explains_limit(self):
        self.blade(provider="shell")
        with patch.object(lifecycle.tmuxctl, "capture_all", return_value="shell output"):
            lifecycle.snapshot(self.name)
        archive = lifecycle.archive_read(self.name)
        self.assertFalse(archive["transcript_available"])
        self.assertTrue(archive["metadata"]["recovery_limitations"])

    def test_missing_transcript_preserves_session(self):
        self.blade()
        with patch.object(winddown.tmuxctl, "send_text") as send, patch.object(winddown.tmuxctl, "kill") as kill:
            result = winddown.run(self.name)
        self.assertEqual(result, "preserved")
        send.assert_not_called()
        kill.assert_not_called()
        self.assertEqual(store.get(self.name)["state"], "attention")

    def test_checkpoint_timeout_never_sends_exit_or_kills(self):
        self.blade()
        self.transcript()
        with patch.object(winddown.tmuxctl, "capture_all", return_value="screen"), \
             patch.object(winddown.tmuxctl, "send_text") as send, \
             patch.object(winddown.tmuxctl, "kill") as kill, \
             patch.object(winddown, "_wait_for_stop", return_value="timeout"):
            self.assertEqual(winddown.run(self.name, "checkpoint"), "preserved")
        send.assert_called_once_with(self.name, "checkpoint")
        kill.assert_not_called()
        self.assertEqual(store.get(self.name)["state"], "attention")

    def test_exit_timeout_keeps_archived_session_alive(self):
        self.blade()
        self.transcript()
        with patch.object(winddown.tmuxctl, "capture_all", return_value="screen"), \
             patch.object(winddown.tmuxctl, "send_text") as send, \
             patch.object(winddown.tmuxctl, "kill") as kill, \
             patch.object(winddown, "_wait_for_end", return_value=False):
            self.assertEqual(winddown.run(self.name), "preserved")
        self.assertIsNotNone(store.get(self.name)["archive_path"])
        send.assert_called_once_with(self.name, "/exit")
        kill.assert_not_called()
        self.assertEqual(store.get(self.name)["state"], "attention")

    def test_archive_failure_keeps_session_and_does_not_exit(self):
        self.blade()
        self.transcript()
        with patch.object(lifecycle, "snapshot", side_effect=OSError("disk full")), \
             patch.object(winddown.tmuxctl, "send_text") as send, \
             patch.object(winddown.tmuxctl, "kill") as kill, \
             self.assertLogs("blades.winddown", level="ERROR"):
            winddown._guard(self.name, None, True)
        send.assert_not_called()
        kill.assert_not_called()
        self.assertEqual(store.get(self.name)["state"], "attention")

    def test_confirmed_exit_follows_durable_snapshot(self):
        self.blade()
        self.transcript()
        def send(name, text):
            self.assertEqual(text, "/exit")
            archive = store.get(name)["archive_path"]
            self.assertTrue(Path(archive, "transcript.jsonl").is_file())
            self.assertTrue(Path(archive, "metadata.json").is_file())
        with patch.object(winddown.tmuxctl, "capture_all", return_value="screen"), \
             patch.object(winddown.tmuxctl, "send_text", side_effect=send), \
             patch.object(winddown, "_wait_for_end", return_value=True), \
             patch.object(winddown.tmuxctl, "kill") as kill:
            self.assertEqual(winddown.run(self.name), "closed")
        kill.assert_not_called()
        self.assertEqual(store.get(self.name)["state"], "closed")
        self.assertTrue(lifecycle.archive_read(self.name)["transcript_available"])

    def test_late_hooks_do_not_resurrect_closed_sessions(self):
        self.blade()
        store.set_state(self.name, "closed")
        store.apply_hook(self.name, "Stop", {})
        self.assertEqual(store.get(self.name)["state"], "closed")

    def test_handoff_preserved_until_explicit_consumption_and_unique_archive(self):
        self.blade()
        path = Path(prompts.write_handoff("test", self.name, str(self.project), "first"))
        self.assertTrue(path.exists())
        self.assertFalse(prompts.consume_handoff("test", expected_digest="wrong"))
        self.assertTrue(path.exists())
        prompts.write_handoff("test", self.name, str(self.project), "second")
        self.assertIn("second", path.read_text())
        self.assertTrue(prompts.consume_handoff("test"))
        notes = list(Path(config.path("handoff/archive")).glob("*.md"))
        self.assertEqual(len(notes), 2)
        self.assertTrue(any("first" in note.read_text() for note in notes))


if __name__ == "__main__":
    unittest.main()
