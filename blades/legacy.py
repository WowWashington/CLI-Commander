"""Legacy Terminal.app Claude sessions: discover, message, wind down, migrate.

A live process cannot be moved into tmux, so "migrate" means: close the
Terminal session cleanly (saving state + handoff) and open a blade for the
same project, optionally resuming the same Claude conversation (--resume).
"""
import glob
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid

from . import config, projects, prompts, store, tmuxctl

log = logging.getLogger("blades.legacy")
_active = {}
_lock = threading.RLock()
_failures = {}
legacy_state = {}  # exact session_id -> hook evidence, never inferred from screen silence
HOOK_FRESH_SECONDS = 120

SEND_AS = """
on run argv
  set target to item 1 of argv
  set msg to item 2 of argv
  tell application "Terminal"
    repeat with w in windows
      repeat with t in tabs of w
        if (tty of t) is target then
          do script msg in t
          return "sent"
        end if
      end repeat
    end repeat
  end tell
  return "notfound"
end run
"""

CONTENTS_AS = """
on run argv
  set target to item 1 of argv
  tell application "Terminal"
    repeat with w in windows
      repeat with t in tabs of w
        if (tty of t) is target then
          return (history of t) as text
        end if
      end repeat
    end repeat
  end tell
  return ""
end run
"""

TABS_AS = """
tell application "Terminal"
  set out to ""
  repeat with w in windows
    repeat with t in tabs of w
      set out to out & (tty of t) & "\n"
    end repeat
  end repeat
  return out
end tell
"""

def _osa(script, *args, timeout=15):
    try:
        r = subprocess.run(["osascript", "-", *args], input=script, capture_output=True,
                           text=True, timeout=timeout)
        if r.returncode != 0:
            log.warning("osascript: %s", r.stderr.strip())
        return r.stdout.rstrip("\n")
    except Exception as e:
        log.warning("osascript failed: %s", e)
        return ""


def _tmux_ttys():
    r = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{pane_tty}"],
                       capture_output=True, text=True)
    return {t.replace("/dev/", "") for t in r.stdout.split() if t}


def terminal_ttys():
    return {t.replace("/dev/", "") for t in _osa(TABS_AS).split("\n") if t}


def list_sessions():
    """Interactive claude processes on a Terminal tty that are not blades."""
    out = []
    skip = _tmux_ttys()
    ps = projects.sh(["ps", "-axo", "pid,tty,etime,command"])
    slug_by_path = {p["path"].rstrip("/"): p for p in projects.parse_projects_md()}
    with _lock:
        state_snapshot = list(legacy_state.values())
        failures = dict(_failures)
        migrating = set(_active)
    for line in ps.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4 or not parts[1].startswith("ttys") or parts[1] in skip:
            continue
        if not re.search(r"(^|/)claude$", parts[3].strip()):
            continue
        pid, tty, etime = int(parts[0]), parts[1], parts[2]
        cwd = ""
        for f in projects.sh(["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"]).splitlines():
            if f.startswith("n"):
                cwd = f[1:]
        proj = slug_by_path.get(cwd.rstrip("/"))
        states = [v for v in state_snapshot if v["cwd"].rstrip("/") == cwd.rstrip("/")
                  and v.get("pid") == pid and v.get("tty") == _dev(tty)]
        states.sort(key=lambda v: v["ts"], reverse=True)
        out.append({
            "pid": pid, "tty": tty, "etime": etime, "cwd": cwd,
            "slug": proj["slug"] if proj else None,
            "project": proj["name"] if proj else os.path.basename(cwd.rstrip("/")) or cwd,
            "state": states[0]["state"] if states else "unknown",
            "note": failures.get(pid) or (states[0]["note"] if states else ""),
            "migration_error": failures.get(pid),
            "migrating": pid in migrating,
        })
    out.sort(key=lambda s: s["project"].lower())
    return out


def get(pid):
    for s in list_sessions():
        if s["pid"] == pid:
            return s
    return None


def _dev(tty):
    return tty if tty.startswith("/dev/") else "/dev/" + tty


def send(tty, text):
    """Two-step send: text, then a bare CR so the Claude composer submits."""
    tty = _dev(tty)
    r = _osa(SEND_AS, tty, text)
    if r != "sent":
        raise RuntimeError(f"terminal tab for {tty} not found")
    time.sleep(0.4)
    _osa(SEND_AS, tty, "")
    return True


def contents(tty, lines=60):
    txt = _osa(CONTENTS_AS, _dev(tty), timeout=20)
    return "\n".join(txt.splitlines()[-lines:])


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _encoded_dirs(cwd):
    cwd = cwd.rstrip("/")
    base = os.path.join(config.HOME, ".claude", "projects")
    cands = {re.sub(r"[^A-Za-z0-9]", "-", cwd), cwd.replace("/", "-")}
    return [os.path.join(base, c) for c in cands if os.path.isdir(os.path.join(base, c))]


def transcript_candidates(cwd, limit=6):
    """Recent Claude transcripts for this cwd: session id, mtime, first ask."""
    files = []
    for d in _encoded_dirs(cwd):
        files += glob.glob(os.path.join(d, "*.jsonl"))
    files.sort(key=os.path.getmtime, reverse=True)
    out = []
    for f in files[:limit]:
        first = ""
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        j = json.loads(line)
                    except ValueError:
                        continue
                    if j.get("type") == "user":
                        c = j.get("message", {}).get("content")
                        if isinstance(c, list):
                            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                        if isinstance(c, str) and c.strip() and not c.startswith("<"):
                            first = c.strip()[:140]
                            break
        except OSError:
            pass
        out.append({"session_id": os.path.splitext(os.path.basename(f))[0],
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(f))),
                    "first_ask": first})
    return out


def _evidence(session, session_id, idle=False, after=None):
    """Require exact, process-bound hook evidence before touching a legacy CLI."""
    if not isinstance(session_id, str) or not re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", session_id):
        raise RuntimeError("Choose the exact Claude conversation ID; the Terminal session was preserved")
    with _lock:
        evidence = dict(legacy_state.get(session_id) or {})
    if not evidence or os.path.realpath(evidence.get("cwd", "")) != os.path.realpath(session["cwd"]):
        raise RuntimeError("No matching Claude hook evidence for this conversation; the Terminal session was preserved")
    transcript = evidence.get("transcript_path")
    if not transcript or os.path.basename(transcript) != session_id + ".jsonl" or not os.path.isfile(transcript):
        raise RuntimeError("The exact conversation transcript is unavailable; the Terminal session was preserved")
    # A cwd can contain many Claude processes. Never guess its owner from the
    # most recently modified transcript or most recent hook in that directory.
    bound = evidence.get("pid") == session["pid"] and evidence.get("tty") == _dev(session["tty"])
    if not bound:
        files = projects.sh(["/usr/sbin/lsof", "-a", "-p", str(session["pid"]), "-Fn"])
        bound = any(line.startswith("n") and os.path.realpath(line[1:]) == os.path.realpath(transcript)
                    for line in files.splitlines())
    if not bound:
        raise RuntimeError("Cannot safely match this conversation to the selected Terminal process; it was preserved")
    if idle:
        cutoff = max(time.time() - HOOK_FRESH_SECONDS, after or 0)
        if (evidence.get("event") != "Stop" or evidence.get("state") != "idle"
                or evidence.get("stop_hook_active") or evidence.get("active_subagents")
                or evidence.get("ts", 0) <= cutoff):
            raise RuntimeError("A fresh completed Stop hook is required; the Terminal session was preserved")
    return evidence


def _wait_stop(session, session_id, prompt, since, timeout):
    deadline = time.monotonic() + timeout
    expected = hashlib.sha256(prompt.strip().encode()).hexdigest()
    while time.monotonic() < deadline:
        if not alive(session["pid"]):
            raise RuntimeError("The source process ended before checkpoint completion was confirmed")
        with _lock:
            current = dict(legacy_state.get(session_id) or {})
        if (current.get("event") == "Stop" and current.get("state") == "idle"
                and current.get("ts", 0) > since and current.get("last_prompt_at", 0) > since
                and current.get("last_prompt_hash") == expected and not current.get("stop_hook_active")):
            return _evidence(session, session_id, idle=True, after=since)
        time.sleep(0.5)
    raise RuntimeError("No matching completed checkpoint hook arrived; the Terminal session was preserved")


def _exit_and_close(tty, pid, timeout, session_id=None, cwd=None, stop_ts=None):
    """Request a graceful CLI exit. Never kill a PID, exit its shell, or close its tab."""
    session = {"tty": tty, "pid": pid, "cwd": cwd or ""}
    evidence = _evidence(session, session_id, idle=True)
    if stop_ts is None or evidence["ts"] != stop_ts:
        raise RuntimeError("Source session changed while saving; the Terminal session was preserved")
    send(tty, "/exit")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive(pid):
            return "left-open"
        time.sleep(0.5)
    if alive(pid):
        raise RuntimeError("Claude did not exit gracefully; its process and Terminal tab were preserved")
    return "left-open"


def validate_migration(pid, mode="fresh", session_id=None, target_slug=None, open_blade=True, **_):
    if mode not in ("fresh", "resume"):
        raise RuntimeError("Unknown migration mode")
    session = get(pid)
    if not session:
        raise RuntimeError(f"no legacy session with pid {pid}")
    slug = target_slug or session["slug"]
    if not slug or not projects.by_slug(slug):
        raise RuntimeError("Choose a known project so the handoff can be saved before closing")
    _evidence(session, session_id, idle=(mode == "resume"))
    return session


def migrate(pid, mode="fresh", text="", session_id=None, open_blade=True, target_slug=None):
    """Migrate only with exact hook evidence and a saved transcript and handoff."""
    s = validate_migration(pid, mode=mode, session_id=session_id, target_slug=target_slug,
                           open_blade=open_blade)
    cfg = config.settings()
    tty, cwd, slug = s["tty"], s["cwd"], target_slug or s["slug"]
    outcome = "resume"
    if mode == "fresh":
        if not text.strip():
            raise RuntimeError("A checkpoint prompt is required; the Terminal session was preserved")
        since = time.time()
        send(tty, text)
        evidence = _wait_stop(s, session_id, text, since, cfg["winddown_timeout_sec"])
        outcome = "checkpoint-complete"
    else:
        evidence = _evidence(s, session_id, idle=True)
    # Save before /exit. Any copy, handoff, or evidence failure leaves the CLI.
    archive = config.path("handoff", "archive", f"legacy-{session_id}-{uuid.uuid4().hex}.jsonl")
    os.makedirs(os.path.dirname(archive), mode=0o700, exist_ok=True)
    with open(evidence["transcript_path"], "rb") as source, open(archive, "xb") as target:
        os.chmod(archive, 0o600)
        shutil.copyfileobj(source, target)
        target.flush()
        os.fsync(target.fileno())
    prompts.write_handoff(slug, f"terminal:{tty}", cwd,
                          f"Conversation: {session_id}\nResume: claude --resume {session_id}\n"
                          f"Original transcript: {evidence['transcript_path']}\n\n" + contents(tty, 60),
                          archive_path=archive)
    closed = _exit_and_close(tty, pid, cfg["exit_timeout_sec"], session_id=session_id,
                             cwd=cwd, stop_ts=evidence["ts"])
    result = {"pid": pid, "outcome": outcome, "tab": closed, "blade": None}
    if open_blade and slug:
        p = projects.by_slug(slug)
        name = tmuxctl.next_name(slug)
        store.create(name, slug, p["name"], p["path"])
        try:
            if mode == "resume":
                store.set_state(name, "starting", session_id=session_id)
                tmuxctl.new_session(name, p["path"], "", resume_id=session_id)
            else:
                tmuxctl.new_session(name, p["path"], prompts.render(p))
        except Exception as exc:
            store.set_state(name, "closed", note=f"migration launch failed; handoff retained: {exc}")
            raise
        result["blade"] = name
    return result


def start_migrate(pid, **kw):
    validate_migration(pid, **kw)
    with _lock:
        if pid in _active:
            return False
        _failures.pop(pid, None)
        t = threading.Thread(target=_guard, args=(pid,), kwargs=kw, daemon=True,
                             name=f"migrate-{pid}")
        _active[pid] = t
        t.start()
    return True


def _guard(pid, **kw):
    try:
        r = migrate(pid, **kw)
        log.info("migrated %s: %s", pid, r)
    except Exception as exc:
        with _lock:
            _failures[pid] = str(exc)
        log.exception("migration failed for pid %s", pid)
    finally:
        with _lock:
            _active.pop(pid, None)


def apply_hook(session_id, cwd, event, payload):
    """Status for non-blade sessions, keyed by Claude session id."""
    payload = payload or {}
    now = time.time()
    with _lock:
        cur = dict(legacy_state.get(session_id) or {"cwd": cwd, "state": "unknown", "note": "", "ts": 0})
        active = set(cur.get("active_subagents", []))
        if event == "SubagentStart":
            active.add(payload.get("agent_id") or "unknown")
        elif event == "SubagentStop":
            active.discard(payload.get("agent_id") or "unknown")
        cur["active_subagents"] = sorted(active)
        state, note = cur["state"], cur["note"]
        if event == "SessionStart":
            state = "unknown"
        elif event == "Stop":
            state = "busy" if payload.get("stop_hook_active") or active else "idle"
        elif event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart"):
            state = "busy"
            note = (payload.get("prompt") or "")[:120]
            if event == "UserPromptSubmit":
                cur["last_prompt_at"] = now
                cur["last_prompt_hash"] = hashlib.sha256((payload.get("prompt") or "").strip().encode()).hexdigest()
        elif event in ("Notification", "PermissionRequest"):
            state, note = "attention", (payload.get("message") or "waiting for input")[:120]
        elif event == "SessionEnd":
            legacy_state.pop(session_id, None)
            return
        cur.update(cwd=cwd or cur["cwd"], state=state, note=note, ts=now, event=event,
                   session_id=session_id, stop_hook_active=bool(payload.get("stop_hook_active")))
        for key in ("transcript_path", "pid", "tty"):
            if payload.get(key) is not None:
                cur[key] = payload[key]
        if cur.get("tty"):
            cur["tty"] = _dev(cur["tty"])
        legacy_state[session_id] = cur
