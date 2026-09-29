"""Durable session history and evidence-based provider activity.

An idle label is not permission to close a session. Only an explicit provider
completion event establishes ``idle_since``; screen silence never does.
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from . import config

_lock = threading.RLock()
_conn = None
_blade_locks = {}

SCHEMA = """
CREATE TABLE IF NOT EXISTS blades (
  name TEXT PRIMARY KEY,
  slug TEXT NOT NULL,
  project TEXT NOT NULL,
  cwd TEXT NOT NULL,
  state TEXT NOT NULL,
  session_id TEXT,
  note TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  closed_at REAL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  blade TEXT NOT NULL,
  event TEXT NOT NULL,
  payload TEXT,
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ev_blade_ts ON events(blade, ts);
"""
MIGRATIONS = {
    "provider": "TEXT NOT NULL DEFAULT 'claude'",
    "last_input_at": "REAL",
    "last_activity_at": "REAL",
    "idle_since": "REAL",
    "activity_source": "TEXT",
    "transcript_path": "TEXT",
    "archive_path": "TEXT",
    "idle_deadline": "REAL",
    "active_subagents": "TEXT NOT NULL DEFAULT '[]'",
}
COMPLETION_SOURCES = {"claude:Stop", "codex:turn-complete", "gemini:AfterAgent"}


def blade_lock(name):
    """Serialize a session's input, provider events and final exit decision."""
    with _lock:
        return _blade_locks.setdefault(name, threading.RLock())


def conn():
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(config.path("blades.db"), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            columns = {r[1] for r in _conn.execute("PRAGMA table_info(blades)")}
            for name, definition in MIGRATIONS.items():
                if name not in columns:
                    _conn.execute(f"ALTER TABLE blades ADD COLUMN {name} {definition}")
            _conn.execute("UPDATE blades SET provider='shell' WHERE note='shell' OR note LIKE 'quick: %'")
            _conn.execute("UPDATE blades SET last_input_at=created_at WHERE last_input_at IS NULL")
            _conn.execute("UPDATE blades SET last_activity_at=updated_at WHERE last_activity_at IS NULL")
            _conn.commit()
            os.chmod(config.path("blades.db"), 0o600)
        return _conn


def _row(r):
    return dict(r) if r else None


def create(name, slug, project, cwd, provider="claude"):
    if provider not in ("claude", "codex", "gemini", "shell"):
        raise ValueError("unknown provider")
    now = time.time()
    with _lock:
        # A reused name must never erase a previous session's history.
        conn().execute(
            "INSERT INTO blades(name,slug,project,cwd,state,provider,created_at,updated_at,last_input_at,last_activity_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (name, slug, project, cwd, "starting", provider, now, now, now, now))
        conn().commit()
    return get(name)


def get(name):
    with _lock:
        return _row(conn().execute("SELECT * FROM blades WHERE name=?", (name,)).fetchone())


def live():
    with _lock:
        return [dict(r) for r in conn().execute(
            "SELECT * FROM blades WHERE state != 'closed' ORDER BY created_at")]


def recent_closed(limit=20):
    with _lock:
        return [dict(r) for r in conn().execute(
            "SELECT * FROM blades WHERE state='closed' ORDER BY closed_at DESC LIMIT ?", (limit,))]


def update(name, **fields):
    allowed = set(MIGRATIONS) | {"state", "note", "session_id", "updated_at", "closed_at"}
    if not fields or not set(fields) <= allowed:
        raise ValueError("invalid session fields")
    with _lock:
        conn().execute(f"UPDATE blades SET {', '.join(k + '=?' for k in fields)} WHERE name=?",
                       [*fields.values(), name])
        conn().commit()


def set_state(name, state, note=None, session_id=None):
    now = time.time()
    fields = {"state": state, "updated_at": now}
    if note is not None:
        fields["note"] = note
    if session_id:
        fields["session_id"] = session_id
    if state not in ("idle", "closing"):
        fields.update(idle_since=None, idle_deadline=None)
    if state == "closed":
        fields["closed_at"] = now
    update(name, **fields)


def mark_activity(name, state, source, session_id=None, transcript_path=None, note=None, ts=None):
    """Record provider evidence. A completion is the only source of idle proof."""
    now = time.time() if ts is None else ts
    with blade_lock(name):
        b = get(name)
        if not b or b["state"] == "closed":
            return
        fields = {"state": state, "updated_at": now, "last_activity_at": now,
                  "activity_source": source, "idle_deadline": None,
                  "idle_since": now if source in COMPLETION_SOURCES and state in ("idle", "closing") else None}
        if session_id:
            fields["session_id"] = session_id
        if transcript_path:
            fields["transcript_path"] = transcript_path
        if note is not None:
            fields["note"] = note
        if state == "closed":
            fields["closed_at"] = now
        update(name, **fields)


def record_input(name, ts=None):
    now = time.time() if ts is None else ts
    with blade_lock(name):
        b = get(name)
        if not b:
            raise RuntimeError(f"unknown blade {name}")
        if b["state"] in ("closing", "closed"):
            raise RuntimeError("session is closing or closed")
        update(name, last_input_at=now, last_activity_at=now, updated_at=now,
               activity_source="human:input", idle_since=None, idle_deadline=None,
               state="unknown" if b["state"] == "idle" else b["state"])


def record_event(blade, event, payload):
    now = time.time()
    with _lock:
        conn().execute("INSERT INTO events(blade,event,payload,ts) VALUES(?,?,?,?)",
                       (blade, event, json.dumps(payload) if payload else None, now))
        conn().commit()
    return now


def events_since(blade, ts, event=None):
    with _lock:
        q = "SELECT * FROM events WHERE blade=? AND ts>?"
        args = [blade, ts]
        if event:
            q += " AND event=?"
            args.append(event)
        return [dict(r) for r in conn().execute(q + " ORDER BY ts, id", args)]


def last_event(blade):
    with _lock:
        return _row(conn().execute(
            "SELECT * FROM events WHERE blade=? ORDER BY ts DESC, id DESC LIMIT 1", (blade,)).fetchone())


_OPENING_PROMPT_PREFIXES = ("You are starting a fresh CLI Commander", "You are starting a fresh CLI Blades")


def apply_hook(blade, event, payload):
    """Apply Claude evidence; hooks arriving after closure cannot reopen a row."""
    with blade_lock(blade):
        b = get(blade)
        if not b:
            return None
        payload = payload or {}
        ts = record_event(blade, event, payload)
        if b["state"] == "closed":
            return "closed"
        sid, transcript = payload.get("session_id"), payload.get("transcript_path")
        cur = b["state"]
        new, note = cur, None
        source = f"claude:{event}"
        if event == "SessionStart":
            # A fresh provider lifecycle discards stale child/tool registrations.
            # SessionStart itself still never establishes idle proof.
            update(blade, active_subagents="[]")
            new, note = "idle", ""
        elif event == "UserPromptSubmit":
            new = "closing" if cur == "closing" else "busy"
            prompt = (payload.get("prompt") or "").strip()
            note = "" if prompt.startswith(_OPENING_PROMPT_PREFIXES) else prompt.splitlines()[0][:120] if prompt else ""
        elif event == "SubagentStart":
            agents = set(json.loads(b.get("active_subagents") or "[]"))
            agents.add(str(payload.get("agent_id") or "unknown"))
            update(blade, active_subagents=json.dumps(sorted(agents)))
            new = "closing" if cur == "closing" else "busy"
        elif event == "SubagentStop":
            agents = set(json.loads(b.get("active_subagents") or "[]"))
            if payload.get("agent_id"):
                agents.discard(str(payload["agent_id"]))
            update(blade, active_subagents=json.dumps(sorted(agents)))
            new = "closing" if cur == "closing" else "busy"
        elif event == "Stop":
            # stop_hook_active means another Stop hook has asked Claude to keep
            # working; that is not completion evidence for a safe shutdown.
            if payload.get("stop_hook_active") or json.loads(b.get("active_subagents") or "[]"):
                new = "closing" if cur == "closing" else "busy"
                source = "claude:Stop-pending"
                if any(str(item).startswith("tool:") for item in json.loads(b.get("active_subagents") or "[]")):
                    note = "Background tool may still be running; automatic close paused"
            else:
                new = "closing" if cur == "closing" else "idle"
                note = "" if cur != "closing" else None
        elif event in ("Notification", "PermissionRequest"):
            msg = payload.get("message") or ""
            ntype = payload.get("notification_type") or ""
            if event == "PermissionRequest" or "permission" in ntype or "permission" in msg.lower():
                new, note = "attention", msg[:160] or "needs permission"
            elif ntype == "idle_prompt":
                # Claude's idle notification is informational; only a prior
                # Stop can prove there is no unfinished work.
                if b.get("idle_since") and b.get("activity_source") in COMPLETION_SOURCES:
                    if sid or transcript:
                        update(blade, **{k: v for k, v in {"session_id": sid, "transcript_path": transcript}.items() if v})
                    return cur
                new, note = "attention", msg[:160] or "waiting for input"
            elif "waiting" in msg.lower():
                new, note = "attention", msg[:160] or "waiting for input"
            else:
                note = msg[:160]
        elif event == "SessionEnd":
            new = "closed"
        elif event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            tool_input = payload.get("tool_input") or {}
            if isinstance(tool_input, dict) and tool_input.get("run_in_background") is True:
                # Main-turn Stop does not prove a background Bash/tool process
                # exited. Retain this conservative blocker until SessionStart;
                # neither PostToolUse nor a failed/unknown TaskStop is proof.
                agents = set(json.loads(b.get("active_subagents") or "[]"))
                agents.add("tool:" + str(payload.get("tool_use_id") or "unknown"))
                update(blade, active_subagents=json.dumps(sorted(agents)))
                note = "Background tool may still be running; automatic close paused"
            new = "closing" if cur == "closing" else "busy"
        mark_activity(blade, new, source, session_id=sid, transcript_path=transcript,
                      note=note, ts=ts)
        return new


def apply_gemini_hook(blade, event, payload):
    """Apply Gemini hooks only to the exact provider, conversation and project.

    Per-launch hooks supply common session_id, cwd and transcript_path fields.
    A stale callback must never attach another conversation's transcript or
    establish idle proof for the current session.
    """
    from . import lifecycle
    event = event.removeprefix("Gemini")
    known = {"SessionStart", "BeforeAgent", "AfterAgent", "BeforeTool", "AfterTool", "BeforeModel",
             "Notification", "SessionEnd"}
    if event not in known or not isinstance(payload, dict):
        raise RuntimeError("invalid Gemini hook event")
    with blade_lock(blade):
        b = get(blade)
        if not b or b["provider"] != "gemini":
            raise RuntimeError("Gemini hook provider does not match the session")
        if b["state"] == "closed":
            return "closed"
        sid, cwd = payload.get("session_id"), payload.get("cwd")
        try:
            valid_sid = isinstance(sid, str) and str(uuid.UUID(sid)) == sid.lower()
        except (ValueError, AttributeError):
            valid_sid = False
        if not valid_sid or (b.get("session_id") and b["session_id"] != sid):
            raise RuntimeError("Gemini hook conversation ID does not match the session")
        if not isinstance(cwd, str) or Path(cwd).resolve() != Path(b["cwd"]).resolve():
            raise RuntimeError("Gemini hook project directory does not match the session")
        transcript = payload.get("transcript_path") or b.get("transcript_path")
        transcript_available = False
        if transcript:
            candidate = dict(b, session_id=sid)
            if not lifecycle._allowed_transcript(transcript, "gemini"):
                raise RuntimeError("Gemini transcript is outside its expected session directory")
            if Path(transcript).is_file():
                lifecycle._validate_gemini_transcript(transcript, candidate)
                transcript_available = True
            else:
                # Gemini predicts this filename before authentication or the
                # first conversation is saved. Register only materialized,
                # identity-validated history. mark_activity(None) deliberately
                # preserves any previously known path, even if now missing.
                transcript = None
        ts = record_event(blade, "Gemini" + event, payload)
        cur, note = b["state"], None
        new, source = cur, "gemini:" + event
        if event == "SessionStart":
            update(blade, active_subagents="[]")
            new, note = "idle", ""
        elif event == "BeforeAgent":
            new = "closing" if cur == "closing" else "busy"
            prompt = str(payload.get("prompt") or "").strip()
            note = "" if prompt.startswith(_OPENING_PROMPT_PREFIXES) else prompt.splitlines()[0][:120] if prompt else ""
        elif event == "AfterAgent":
            if not transcript_available:
                new = "closing" if cur == "closing" else "attention"
                source, note = "gemini:AfterAgent-pending", "Gemini transcript is not saved yet; automatic close paused"
            elif payload.get("stop_hook_active") or payload.get("error") or json.loads(b.get("active_subagents") or "[]"):
                new = "closing" if cur == "closing" else "attention"
                source, note = "gemini:AfterAgent-pending", "Completion is uncertain; automatic close paused"
            else:
                new = "closing" if cur == "closing" else "idle"
                note = None if cur == "closing" else ""
        elif event in ("BeforeTool", "AfterTool", "BeforeModel"):
            new = "closing" if cur == "closing" else "busy"
            tool_input = payload.get("tool_input") or {}
            if isinstance(tool_input, dict) and (tool_input.get("run_in_background") is True or tool_input.get("is_background") is True):
                agents = set(json.loads(b.get("active_subagents") or "[]"))
                agents.add("tool:" + str(payload.get("tool_use_id") or "unknown"))
                update(blade, active_subagents=json.dumps(sorted(agents)))
                note = "Background tool may still be running; automatic close paused"
        elif event == "Notification":
            new = "attention"
            note = str(payload.get("message") or "Gemini needs your attention")[:160]
        elif event == "SessionEnd":
            # Gemini also ends a conversation for /clear and logout. Only a
            # dead pane (checked by the close orchestrator) proves CLI exit.
            new = "unknown"
            note = ("Gemini conversation cleared; open a fresh Blade to track the new conversation"
                    if payload.get("reason") == "clear"
                    else "Gemini conversation ended; awaiting process or session confirmation")
        mark_activity(blade, new, source, session_id=sid, transcript_path=transcript, note=note, ts=ts)
        return new
