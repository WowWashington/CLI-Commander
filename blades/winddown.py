"""Graceful close that fails open: uncertainty always preserves the session."""
import logging
import threading
import time

from . import config, lifecycle, store, tmuxctl

log = logging.getLogger("blades.winddown")
_active = {}
_lock = threading.Lock()
_cwd_locks = {}


def _cwd_lock(cwd):
    with _lock:
        return _cwd_locks.setdefault(cwd.rstrip("/"), threading.Lock())


def untouched(name):
    """Kept for callers; unmodified files never prove a CLI is safe to kill."""
    return False


def is_active(name):
    with _lock:
        return name in _active


def _preserve(name, reason):
    b = store.get(name)
    if b and b["state"] != "closed":
        store.set_state(name, "attention", note=f"Session preserved: {reason}")
    return "preserved"


def _wait_for_stop(name, sent_ts, timeout):
    """Require provider completion after the request, then a stable interval.

    A motionless terminal may be a permission prompt or a stalled tool. It is
    deliberately never accepted as evidence that a model finished.
    """
    deadline = time.monotonic() + timeout
    stable_proof, stable_at = None, None
    while time.monotonic() < deadline:
        b = store.get(name)
        if not b or b["state"] == "closed" or not tmuxctl.exists(name):
            return "gone"
        if b["state"] == "attention":
            return "attention"
        finished = (b.get("idle_since") or 0) > sent_ts and b.get("activity_source") in store.COMPLETION_SOURCES
        if finished:
            token = lifecycle.proof(b)
            if token != stable_proof:
                stable_proof, stable_at = token, time.monotonic()
            elif time.monotonic() - stable_at >= 3:
                return "stopped"
        else:
            stable_proof, stable_at = None, None
        time.sleep(0.5)
    return "timeout"


def _wait_for_end(name, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        b = store.get(name)
        if not tmuxctl.exists(name) or tmuxctl.pane_dead(name) or (b and b["state"] == "closed"):
            return True
        time.sleep(0.5)
    return False


def run(name, text=None, write_handoff=True, automatic=False):
    b = store.get(name)
    if not b:
        raise RuntimeError(f"unknown blade {name}")
    # Serializing by project avoids multiple checkpoint prompts changing the
    # same STATE.md at once. Revalidate the session after acquiring this lock.
    with _cwd_lock(b["cwd"]):
        return _run_locked(name, text, automatic, config.settings())


def _run_locked(name, text, automatic, settings):
    with store.blade_lock(name):
        b = store.get(name)
        if not lifecycle.proven_idle(b):
            return _preserve(name, "provider has not confirmed it is idle")
        if automatic and name in config.protected():
            return "protected"
        if not b.get("transcript_path"):
            return _preserve(name, "provider transcript has not been located; automatic recovery is not yet available")
        if automatic:
            activity = max(b.get("last_input_at") or 0, b.get("last_activity_at") or 0)
            timeout = float(settings.get("idle_timeout_min", 1440)) * 60
            if timeout <= 0 or time.time() - activity < timeout:
                return "active"
            if not b.get("idle_deadline") or time.time() < b["idle_deadline"]:
                return "active"
        token = lifecycle.proof(b)
        if text and not automatic:
            # A user-defined closing command might itself exit the CLI. Save a
            # recovery baseline before delivering any such command.
            lifecycle.snapshot(name, reason="before-checkpoint")
            store.set_state(name, "closing", note="Waiting for checkpoint completion; session remains recoverable")
            sent_ts = time.time()
            try:
                tmuxctl.send_text(name, text)
            except Exception as exc:
                return _preserve(name, f"checkpoint could not be sent ({exc})")
    if text and not automatic:
        outcome = _wait_for_stop(name, sent_ts, float(settings.get("winddown_timeout_sec", 900)))
        if outcome != "stopped":
            if outcome == "gone":
                # Keep the pre-command archive; reconcile can capture the
                # remaining final output when the retained pane is available.
                store.set_state(name, "closed", note="CLI exited during checkpoint; prior history archived")
                return "gone"
            return _preserve(name, f"checkpoint {outcome}; review and retry")
        b = store.get(name)
        token = lifecycle.proof(b)

    with store.blade_lock(name):
        current = store.get(name)
        if not current or current["state"] not in ("idle", "closing") or lifecycle.proof(current) != token:
            return _preserve(name, "activity changed before close")
        if current.get("activity_source") not in store.COMPLETION_SOURCES:
            return _preserve(name, "completion could not be confirmed")
        archive = lifecycle.snapshot(name, reason="inactivity" if automatic else "wind-down")
        current = store.get(name)
        if lifecycle.proof(current) != token or not lifecycle.snapshot_unchanged(archive):
            return _preserve(name, "activity changed while saving history")
        store.set_state(name, "closing", note="History saved; waiting for CLI to exit")
        tmuxctl.send_text(name, "/quit" if current["provider"] == "gemini" else "/exit")

    if not _wait_for_end(name, float(settings.get("exit_timeout_sec", 90))):
        # Never interrupt or kill on a deadline. /exit could have been entered
        # into a permission dialog, or the CLI could still be working.
        return _preserve(name, "CLI did not confirm exit; history was saved")
    # A launcher may leave tmux alive briefly after SessionEnd. Let that normal
    # exit finish; do not kill a pane that could have received newer input.
    store.set_state(name, "closed", note="Archived after inactivity" if automatic else "Checkpoint and history archived")
    return "closed"


def start(name, text=None, write_handoff=True, automatic=False):
    with store.blade_lock(name):
        b = store.get(name)
        if not b:
            raise RuntimeError(f"unknown blade {name}")
        if not lifecycle.proven_idle(b):
            raise RuntimeError("Close requires confirmed idle status; busy, waiting, shell and unknown sessions stay open")
        with _lock:
            if name in _active:
                return False
            thread = threading.Thread(target=_guard, args=(name, text, write_handoff, automatic),
                                      daemon=True, name=f"winddown-{name}")
            _active[name] = thread
            thread.start()
    return True


def _guard(name, text, write_handoff, automatic=False):
    try:
        run(name, text, write_handoff, automatic)
    except Exception as exc:
        log.exception("wind-down failed for %s; preserving session", name)
        _preserve(name, f"archive or close failed ({exc})")
    finally:
        with _lock:
            _active.pop(name, None)
