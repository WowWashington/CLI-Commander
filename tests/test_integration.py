"""Opt-in real tmux, HTTP and WebSocket exercise, without model credentials.

Run: BLADES_INTEGRATION=1 .venv/bin/python -m unittest discover -s tests -p test_integration.py -v
Only a uniquely named tmux socket and temporary provider/project files are used.
"""
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid
from unittest.mock import patch


FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time, urllib.request
root = pathlib.Path(os.environ["BLADES_FIXTURE_ROOT"])
blade = os.environ["BLADE_ID"]
sid = os.environ["BLADES_FIXTURE_SESSION"]
args = sys.argv[1:]
(root / (blade + ".args.json")).write_text(json.dumps(args))
transcript = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "fixture" / (sid + ".jsonl")
transcript.parent.mkdir(parents=True, exist_ok=True)
def append(kind, text):
    with transcript.open("a") as f:
        f.write(json.dumps({"type": kind, "session_id": sid, "text": text}) + "\n")
        f.flush()
        os.fsync(f.fileno())
def hook(event, **extra):
    payload = dict(session_id=sid, transcript_path=str(transcript), **extra)
    data = json.dumps(dict(blade=blade, event=event, payload=payload)).encode()
    req = urllib.request.Request("http://127.0.0.1:" + os.environ["BLADES_PORT"] + "/hook",
                                 data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError("hook failed")
append("session_meta", "resumed" if "--resume" in args else "started")
hook("SessionStart")
print("FIXTURE READY " + sid, flush=True)
hook("Stop")
for line in sys.stdin:
    line = line.rstrip("\r\n")
    if line == "/exit":
        # Verify durability from the provider process before honoring exit.
        home = pathlib.Path(os.environ["BLADES_HOME"])
        archives = list((home / "history").glob("*/metadata.json"))
        durable = any(json.loads(p.read_text())["session"]["name"] == blade and
                      (p.parent / "transcript.jsonl").is_file() for p in archives)
        (root / (blade + ".exit.json")).write_text(json.dumps({"archive_before_exit": durable}))
        append("exit", line)
        print("FIXTURE EXIT", flush=True)
        hook("SessionEnd")
        break
    hook("UserPromptSubmit", prompt=line)
    append("user", line)
    print("FIXTURE RUNNING " + line, flush=True)
    time.sleep(0.15)
    append("assistant", "ECHO " + line)
    print("ECHO " + line, flush=True)
    hook("Stop")
'''


@unittest.skipUnless(os.environ.get("BLADES_INTEGRATION") == "1", "set BLADES_INTEGRATION=1 for isolated real tmux test")
class RealTerminalIntegration(unittest.TestCase):
    def setUp(self):
        if not shutil.which("tmux") or not Path("/bin/zsh").exists():
            self.skipTest("tmux and /bin/zsh are required")
        self.temp = tempfile.TemporaryDirectory(prefix="blades-integration-")
        self.root = Path(self.temp.name)
        self.socket = "blades-test-" + uuid.uuid4().hex
        self.sid = str(uuid.uuid4())
        self.home = self.root / "blades"
        self.project = self.root / "project"
        self.project.mkdir()
        for sub in ("projects", "handoff/archive", "run"):
            (self.home / sub).mkdir(parents=True, exist_ok=True)
        self.provider_dir = self.root / "claude"
        index = self.root / "projects.md"
        index.write_text(f"| 1 | Fixture | fixture | `{self.project}` | — | — | — | Ready |\n")
        fake = self.root / "fake-claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o700)
        self.env = patch.dict(os.environ, {
            "BLADES_HOME": str(self.home), "BLADES_PROJECTS_MD": str(index),
            "BLADES_TMUX_SOCKET": self.socket, "CLAUDE_CONFIG_DIR": str(self.provider_dir),
            "BLADES_CLAUDE_BIN": str(fake), "BLADES_FIXTURE_ROOT": str(self.root),
            "BLADES_FIXTURE_SESSION": self.sid,
        })
        self.env.start()
        self.patches = []
        self.clients = []
        self.server = None
        self.thread = None
        self.store = None
        self.addCleanup(self.cleanup)
        from blades import config, store, lifecycle
        self.config, self.store, self.lifecycle = config, store, lifecycle
        if store._conn is not None:
            store._conn.close()
            store._conn = None
        for attr, value in {"CONFIG_DIR": str(self.home), "PROJECTS_MD": str(index),
                            "CLAUDE_BIN": str(fake)}.items():
            p = patch.object(config, attr, value)
            p.start()
            self.patches.append(p)
        # Patch config before importing the application: no real provider, DB,
        # project, service listener or tmux socket can be selected by this test.
        self.app_module = importlib.import_module("blades.app")
        from blades import security
        allowed = patch.object(security, "allowed_hosts", return_value={"127.0.0.1", "localhost"})
        allowed.start()
        self.patches.append(allowed)
        config.save_settings({"winddown_timeout_sec": 8, "exit_timeout_sec": 5,
                              "idle_timeout_min": 60, "idle_grace_sec": 120,
                              "tmux_cols": 100, "tmux_rows": 30})
        config.save_commands([{"id": "wind-down", "label": "Fixture checkpoint",
                               "close": True, "text": "fixture-checkpoint"}])
        config.save_json(config.path("onboarding.json"), {"version": 1, "completed": True,
                                                        "enabled_providers": ["claude"]})
        from werkzeug.serving import make_server
        self.server = make_server("127.0.0.1", 0, self.app_module.app, threaded=True)
        self.port = self.server.server_port
        port_patch = patch.object(config, "PORT", self.port)
        port_patch.start()
        self.patches.append(port_patch)
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def cleanup(self):
        for client in self.clients:
            try:
                client.close()
            except Exception:
                pass
        # An explicit socket argument is mandatory even during failed setup.
        if self.socket.startswith("blades-test-"):
            subprocess.run(["tmux", "-L", self.socket, "kill-server"],
                           capture_output=True, timeout=5)
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.store is not None and self.store._conn is not None:
            self.store._conn.close()
            self.store._conn = None
        for p in reversed(self.patches):
            p.stop()
        self.env.stop()
        self.temp.cleanup()

    def request(self, path, body=None, method="GET"):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "X-Blades-Request": "1"}
        req = urllib.request.Request(self.origin + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=8) as response:
            return json.load(response)

    def wait_for(self, predicate, description, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.05)
        screen = ""
        if getattr(self, "name", None):
            from blades import tmuxctl
            screen = tmuxctl.capture(self.name, 20)
        self.fail(f"Timed out waiting for {description}; terminal={screen!r}")

    def connect(self, name):
        from simple_websocket import Client
        class BrowserClient(Client):
            def handshake(self):
                # simple-websocket 1.x omits nondefault ports from Host. Browsers
                # include them, which the app's exact Origin check requires.
                hostname = self.host
                self.host = f"{hostname}:{self.port}"
                try:
                    return super().handshake()
                finally:
                    self.host = hostname
        client = BrowserClient(f"ws://127.0.0.1:{self.port}/ws/term/{name}",
                               headers={"Origin": self.origin})
        self.clients.append(client)
        return client

    def receive_until(self, client, expected, timeout=8):
        deadline = time.monotonic() + timeout
        output = ""
        while time.monotonic() < deadline:
            chunk = client.receive(timeout=min(0.5, max(0, deadline - time.monotonic())))
            if chunk:
                output += chunk.decode() if isinstance(chunk, bytes) else chunk
                if expected in output:
                    return output
        self.fail(f"Missing {expected!r} in terminal output {output!r}")

    def test_launch_attach_reconnect_archive_and_exact_resume(self):
        from blades import tmuxctl, winddown
        opened = self.request("/api/blades", {"slug": "fixture", "provider": "claude"}, "POST")
        self.name = opened["name"]
        self.wait_for(lambda: self.lifecycle.proven_idle(self.store.get(self.name)), "initial provider Stop")
        self.assertEqual(self.store.get(self.name)["session_id"], self.sid)
        self.assertTrue(tmuxctl.exists(self.name))
        self.assertFalse(tmuxctl.pane_dead(self.name))
        args = json.loads((self.root / (self.name + ".args.json")).read_text())
        self.assertIn("--settings", args)
        self.assertTrue(any("starting a fresh CLI Commander" in arg for arg in args))

        first, second = self.connect(self.name), self.connect(self.name)
        self.receive_until(first, "FIXTURE READY")
        self.receive_until(second, "FIXTURE READY")
        self.wait_for(lambda: self.lifecycle.idle_status(self.store.get(self.name))["viewers"] == 2,
                      "two terminal viewers")
        first.send("mobile-and-tablet\r")
        self.receive_until(first, "ECHO mobile-and-tablet")
        self.receive_until(second, "ECHO mobile-and-tablet")
        self.wait_for(lambda: self.lifecycle.proven_idle(self.store.get(self.name)), "completed websocket input")

        first.close()
        second.close()
        self.wait_for(lambda: self.lifecycle.idle_status(self.store.get(self.name))["viewers"] == 0,
                      "detached terminal viewers")
        self.assertTrue(tmuxctl.exists(self.name))
        reconnected = self.connect(self.name)
        self.receive_until(reconnected, "ECHO mobile-and-tablet")
        reconnected.send("same-session-after-reconnect\r")
        self.receive_until(reconnected, "ECHO same-session-after-reconnect")
        self.wait_for(lambda: self.lifecycle.proven_idle(self.store.get(self.name)), "reconnected input completed")
        reconnected.close()
        self.wait_for(lambda: self.lifecycle.idle_status(self.store.get(self.name))["viewers"] == 0,
                      "final viewer detached")

        closed = self.request(f"/api/blades/{self.name}/winddown", {}, "POST")
        self.assertTrue(closed["ok"])
        self.wait_for(lambda: self.store.get(self.name)["state"] == "closed" and not winddown.is_active(self.name),
                      "graceful checkpoint and exit", timeout=15)
        self.wait_for(lambda: tmuxctl.pane_dead(self.name), "retained dead pane")
        exit_record = json.loads((self.root / (self.name + ".exit.json")).read_text())
        self.assertTrue(exit_record["archive_before_exit"])
        archive = self.request(f"/api/blades/{self.name}/archive")
        self.assertTrue(archive["transcript_available"])
        self.assertIn("ECHO mobile-and-tablet", archive["screen"])
        self.assertIn("ECHO same-session-after-reconnect", archive["screen"])
        self.assertIn("fixture-checkpoint", archive["screen"])
        self.assertEqual(archive["metadata"]["session"]["session_id"], self.sid)

        # Reconciliation captures the final exit output before dropping tmux.
        listing = self.request("/api/blades")
        self.assertTrue(any(b["name"] == self.name and b["session_id"] == self.sid for b in listing["recent"]))
        self.assertFalse(tmuxctl.exists(self.name))
        before = self.store.get(self.name)
        source = Path(before["transcript_path"])
        source.unlink()  # Simulate provider history cleanup, then exercise restore.
        resumed = self.request(f"/api/blades/{self.name}/resume", {}, "POST")
        resumed_name = resumed["name"]
        self.assertNotEqual(resumed_name, self.name)
        self.wait_for(lambda: self.lifecycle.proven_idle(self.store.get(resumed_name)), "exact conversation resumed")
        resumed_args = json.loads((self.root / (resumed_name + ".args.json")).read_text())
        self.assertEqual(resumed_args[resumed_args.index("--resume") + 1], self.sid)
        self.assertEqual(self.store.get(resumed_name)["session_id"], self.sid)
        self.assertIn("same-session-after-reconnect", source.read_text())
        self.assertEqual(self.store.get(self.name)["state"], "closed")


if __name__ == "__main__":
    unittest.main()
