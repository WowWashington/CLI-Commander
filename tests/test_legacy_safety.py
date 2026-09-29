"""Legacy Terminal migration must fail closed without provider completion proof."""
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

_IMPORT_HOME = tempfile.TemporaryDirectory(prefix="blades-legacy-import-")
with mock.patch.dict(os.environ, {"BLADES_HOME": _IMPORT_HOME.name}):
    from blades import config, legacy


class LegacySafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blades-legacy-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sid = "11111111-2222-3333-4444-555555555555"
        self.transcript = self.root / (self.sid + ".jsonl")
        self.transcript.write_text('{"sessionId":"' + self.sid + '","message":"retained"}\n')
        self.session = {"pid": 345, "tty": "ttys003", "cwd": str(self.root), "slug": "example"}
        self.project = {"slug": "example", "name": "Example", "path": str(self.root)}
        self.patches = [mock.patch.object(config, "CONFIG_DIR", str(self.root / "state")),
                        mock.patch.object(legacy, "legacy_state", {}),
                        mock.patch.object(legacy, "_active", {}),
                        mock.patch.object(legacy, "_failures", {}),
                        mock.patch.object(legacy, "get", return_value=self.session),
                        mock.patch.object(legacy.projects, "by_slug", return_value=self.project),
                        mock.patch.object(legacy.projects, "sh", return_value="")]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def hook(self, event="Stop", **payload):
        value = {"session_id": self.sid, "transcript_path": str(self.transcript),
                 "pid": 345, "tty": "ttys003"}
        value.update(payload)
        legacy.apply_hook(self.sid, str(self.root), event, value)
        return legacy.legacy_state[self.sid]

    def test_no_session_id_or_no_hook_preserves_the_terminal(self):
        with mock.patch.object(legacy, "send") as send:
            for sid in (None, "latest", self.sid):
                with self.subTest(session_id=sid), self.assertRaises(RuntimeError):
                    legacy.migrate(345, mode="resume", session_id=sid)
            send.assert_not_called()

    def test_cwd_alone_cannot_bind_a_conversation_to_a_process(self):
        self.hook(pid=None, tty=None)
        with self.assertRaisesRegex(RuntimeError, "Cannot safely match"):
            legacy.validate_migration(345, mode="resume", session_id=self.sid)
        with mock.patch.object(legacy.projects, "sh", return_value="p345\nn" + str(self.transcript)):
            self.assertEqual(legacy.validate_migration(345, mode="resume", session_id=self.sid), self.session)

    def test_missing_or_wrong_transcript_blocks_migration(self):
        self.hook(transcript_path=str(self.root / "wrong.jsonl"))
        with self.assertRaisesRegex(RuntimeError, "transcript"):
            legacy.validate_migration(345, mode="resume", session_id=self.sid)

    def test_resume_requires_fresh_completed_stop_not_start_or_pending_stop(self):
        for event, fields in (("SessionStart", {}), ("Stop", {"stop_hook_active": True}),
                              ("UserPromptSubmit", {"prompt": "working"}),
                              ("PermissionRequest", {"message": "approve"})):
            with self.subTest(event=event, fields=fields):
                self.hook(event, **fields)
                with self.assertRaisesRegex(RuntimeError, "fresh completed Stop"):
                    legacy.validate_migration(345, mode="resume", session_id=self.sid)
        self.hook()
        legacy.legacy_state[self.sid]["ts"] = time.time() - legacy.HOOK_FRESH_SECONDS - 1
        with self.assertRaisesRegex(RuntimeError, "fresh completed Stop"):
            legacy.validate_migration(345, mode="resume", session_id=self.sid)

    def test_idle_screen_does_not_substitute_for_matching_checkpoint(self):
        self.hook()
        with mock.patch.object(legacy, "contents", return_value="idle looking prompt") as screen, \
                mock.patch.object(legacy, "alive", return_value=True), self.assertRaisesRegex(
                    RuntimeError, "No matching completed checkpoint"):
            legacy._wait_stop(self.session, self.sid, "save now", time.time(), timeout=0)
        screen.assert_not_called()

    def test_main_stop_does_not_close_an_observed_background_subagent(self):
        self.hook("SubagentStart", agent_id="background-job")
        self.hook("Stop")
        with self.assertRaisesRegex(RuntimeError, "fresh completed Stop"):
            legacy.validate_migration(345, mode="resume", session_id=self.sid)

    def test_matching_prompt_and_stop_are_required_for_checkpoint(self):
        since = time.time() - 1
        self.hook("UserPromptSubmit", prompt="save now")
        self.hook("Stop")
        with mock.patch.object(legacy, "alive", return_value=True):
            evidence = legacy._wait_stop(self.session, self.sid, "save now", since, timeout=1)
        self.assertEqual(evidence["last_prompt_hash"], hashlib.sha256(b"save now").hexdigest())
        with mock.patch.object(legacy, "alive", return_value=True), mock.patch.object(
                legacy.time, "monotonic", side_effect=[0, 0.1, 2]), mock.patch.object(legacy.time, "sleep"), \
                self.assertRaises(RuntimeError):
            legacy._wait_stop(self.session, self.sid, "different prompt", since, timeout=1)

    def test_exit_timeout_never_signals_process_or_closes_shell_or_tab(self):
        evidence = self.hook()
        with mock.patch.object(legacy, "send") as send, mock.patch.object(legacy, "alive", return_value=True), \
                mock.patch.object(legacy.os, "kill") as kill, mock.patch.object(legacy, "_osa") as osa, \
                self.assertRaisesRegex(RuntimeError, "did not exit gracefully"):
            legacy._exit_and_close("ttys003", 345, 0, self.sid, str(self.root), evidence["ts"])
        send.assert_called_once_with("ttys003", "/exit")
        kill.assert_not_called()
        osa.assert_not_called()

    def test_activity_after_archive_prevents_graceful_exit_request(self):
        previous = self.hook()
        self.hook("UserPromptSubmit", prompt="new work")
        with mock.patch.object(legacy, "send") as send, self.assertRaises(RuntimeError):
            legacy._exit_and_close("ttys003", 345, 1, self.sid, str(self.root), previous["ts"])
        send.assert_not_called()

    def test_transcript_and_handoff_are_saved_before_graceful_exit(self):
        self.hook()
        order = []

        def handoff(*args, **kwargs):
            order.append("handoff")
            archive = Path(kwargs["archive_path"])
            self.assertEqual(archive.read_bytes(), self.transcript.read_bytes())
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            self.assertIn(self.sid, args[3])
            self.assertTrue(self.transcript.exists())

        def exit_cli(*args, **kwargs):
            order.append("exit")
            return "left-open"

        with mock.patch.object(legacy.prompts, "write_handoff", side_effect=handoff), \
                mock.patch.object(legacy, "contents", return_value="last output"), \
                mock.patch.object(legacy, "_exit_and_close", side_effect=exit_cli), \
                mock.patch.object(legacy.prompts, "consume_handoff") as consume:
            result = legacy.migrate(345, mode="resume", session_id=self.sid, open_blade=False)
        self.assertEqual(order, ["handoff", "exit"])
        self.assertEqual(result["tab"], "left-open")
        consume.assert_not_called()

    def test_archive_failure_keeps_process_and_does_not_exit(self):
        self.hook()
        with mock.patch.object(legacy.shutil, "copyfileobj", side_effect=OSError("disk full")), \
                mock.patch.object(legacy, "_exit_and_close") as exit_cli, self.assertRaises(OSError):
            legacy.migrate(345, mode="resume", session_id=self.sid, open_blade=False)
        exit_cli.assert_not_called()
        self.assertTrue(self.transcript.exists())

    def test_destination_launch_failure_retains_handoff_and_failure_state(self):
        self.hook()
        with mock.patch.object(legacy.prompts, "write_handoff"), \
                mock.patch.object(legacy, "contents", return_value="last output"), \
                mock.patch.object(legacy, "_exit_and_close", return_value="left-open"), \
                mock.patch.object(legacy.tmuxctl, "next_name", return_value="blade-example-2"), \
                mock.patch.object(legacy.store, "create"), mock.patch.object(legacy.store, "set_state") as state, \
                mock.patch.object(legacy.tmuxctl, "new_session", side_effect=RuntimeError("launch failed")), \
                mock.patch.object(legacy.prompts, "consume_handoff") as consume, self.assertRaises(RuntimeError):
            legacy.migrate(345, mode="resume", session_id=self.sid)
        consume.assert_not_called()
        self.assertEqual(state.call_args.args, ("blade-example-2", "closed"))
        self.assertIn("handoff retained", state.call_args.kwargs["note"])

    def test_worker_failure_is_saved_for_status(self):
        with mock.patch.object(legacy, "migrate", side_effect=RuntimeError("checkpoint not confirmed")), \
                mock.patch.object(legacy.log, "exception"):
            legacy._guard(345)
        self.assertEqual(legacy._failures[345], "checkpoint not confirmed")
        self.assertNotIn(345, legacy._active)


if __name__ == "__main__":
    unittest.main()
