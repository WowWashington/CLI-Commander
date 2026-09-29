"""Conservative inactivity cleanup and durable session recovery snapshots."""
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

from . import config, prompts, store, tmuxctl

log = logging.getLogger("blades.lifecycle")
_viewers = {}
_viewer_lock = threading.Lock()


def attach(name):
    with _viewer_lock:
        _viewers[name] = _viewers.get(name, 0) + 1


def detach(name):
    with _viewer_lock:
        _viewers[name] = max(0, _viewers.get(name, 0) - 1)


def record_input(name):
    store.record_input(name)


@contextmanager
def interaction(name):
    """Hold while sending actual user input, so it cannot race a final exit."""
    with store.blade_lock(name):
        record_input(name)
        yield


def keepalive(name):
    """Extend the inactivity timer without inventing new provider evidence."""
    with store.blade_lock(name):
        b = store.get(name)
        if not b or b["state"] in ("closing", "closed"):
            raise RuntimeError("session is closing or closed")
        store.update(name, last_input_at=time.time(), idle_deadline=None)


def proven_idle(b):
    return bool(b and b["state"] == "idle" and b.get("provider") in ("claude", "codex", "gemini")
                and b.get("idle_since") is not None
                and b.get("activity_source") == {"claude": "claude:Stop", "codex": "codex:turn-complete", "gemini": "gemini:AfterAgent"}.get(b.get("provider"))
                and not json.loads(b.get("active_subagents") or "[]"))


def proof(b):
    return (b.get("last_input_at"), b.get("last_activity_at"), b.get("idle_since"),
            b.get("activity_source"), b.get("session_id"))


def idle_status(b, now=None):
    now = time.time() if now is None else now
    activity = max(b.get("last_input_at") or b["created_at"],
                   b.get("last_activity_at") or b["updated_at"])
    deadline = b.get("idle_deadline")
    with _viewer_lock:
        viewers = _viewers.get(b["name"], 0)
    return {"idle_sec": max(0, int(now - activity)),
            "close_in_sec": max(0, int(deadline - now)) if deadline else None,
            "auto_close_eligible": proven_idle(b) and b["name"] not in config.protected(),
            "viewers": viewers}


def _write(path, content):
    with open(path, "x", encoding="utf-8") as f:
        os.chmod(path, 0o600)
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


def _allowed_transcript(path, provider):
    """Hook payloads cannot turn the archive API into an arbitrary file reader."""
    candidate = Path(path).expanduser().resolve()
    if provider == "claude":
        roots = [Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser() / "projects"]
    elif provider == "codex":
        base = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
        roots = [base / "sessions", base / "archived_sessions"]
    elif provider == "gemini":
        roots = [Path(os.environ.get("GEMINI_CLI_HOME", "~")).expanduser() / ".gemini" / "tmp"]
        return (candidate.suffix == ".json" and candidate.parent.name == "chats"
                and candidate.name.startswith("session-")
                and any(candidate.is_relative_to(r.resolve()) for r in roots))
    else:
        return False
    return candidate.suffix == ".jsonl" and any(candidate.is_relative_to(r.resolve()) for r in roots)


def _validate_gemini_transcript(path, blade):
    """Gemini JSON files carry full identities; filenames alone are insufficient."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise RuntimeError("Gemini transcript is unavailable or incomplete") from exc
    expected_hash = hashlib.sha256(str(Path(blade["cwd"]).resolve()).encode()).hexdigest()
    if not isinstance(data, dict) or data.get("sessionId") != blade.get("session_id"):
        raise RuntimeError("Gemini transcript conversation ID does not match the session")
    if data.get("projectHash") != expected_hash:
        raise RuntimeError("Gemini transcript project does not match the session")
    return data


def _transcript_filename(metadata):
    filename = (metadata.get("transcript") or {}).get("archive_file", "transcript.jsonl")
    if filename not in ("transcript.jsonl", "transcript.json"):
        raise RuntimeError("invalid archived transcript filename")
    return filename


def _copy_recovery_directory(source, destination):
    """Copy provider-owned recovery files without following symlinks."""
    source = Path(source)
    if not source.exists():
        return False
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError("unexpected provider recovery directory")
    destination.mkdir(mode=0o700)
    for entry in source.iterdir():
        if entry.is_symlink():
            raise RuntimeError("provider recovery data contains a symlink; close paused")
        target = destination / entry.name
        if entry.is_dir():
            _copy_recovery_directory(entry, target)
        elif entry.is_file():
            with entry.open("rb") as src, target.open("xb") as dst:
                os.chmod(target, 0o600)
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
    return True


def snapshot(name, reason="manual-close"):
    """Atomically publish a unique, complete snapshot before any close action.

    A known transcript that cannot be copied aborts the snapshot and therefore
    automatic closure. Terminal scrollback is bounded by tmux's history limit;
    the provider transcript is the durable record of model conversation.
    """
    with store.blade_lock(name):
        b = store.get(name)
        if not b:
            raise RuntimeError(f"unknown blade {name}")
        root = Path(config.path("history"))
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        # Name is metadata, never an unchecked path component.
        ident = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex}"
        pending = root / ("." + ident + ".tmp")
        target = root / ident
        pending.mkdir(mode=0o700)
        try:
            screen = tmuxctl.capture_all(name)
            if not isinstance(screen, str):
                raise RuntimeError("terminal snapshot failed")
            _write(pending / "terminal.txt", screen)
            source = b.get("transcript_path")
            transcript_meta = None
            if source:
                if not _allowed_transcript(source, b["provider"]):
                    raise RuntimeError("provider transcript is outside its expected session directory")
                before = os.stat(source)
                filename = "transcript.json" if b["provider"] == "gemini" else "transcript.jsonl"
                with open(source, "rb") as src, open(pending / filename, "xb") as dest:
                    os.chmod(pending / filename, 0o600)
                    shutil.copyfileobj(src, dest)
                    dest.flush()
                    os.fsync(dest.fileno())
                after = os.stat(source)
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise RuntimeError("provider transcript changed during snapshot; session preserved")
                if b["provider"] == "gemini":
                    _validate_gemini_transcript(pending / filename, b)
                transcript_meta = {"source": source, "size": before.st_size, "mtime_ns": before.st_mtime_ns,
                                   "archive_file": filename}
            recovery = []
            if source and b["provider"] == "claude":
                sid = b.get("session_id")
                if sid and Path(sid).name == sid and sid not in (".", ".."):
                    candidates = [
                        (Path(source).parent / sid, "session-files"),
                        (Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser() / "file-history" / sid, "file-history"),
                    ]
                    for original, directory in candidates:
                        if _copy_recovery_directory(original, pending / directory):
                            recovery.append(directory)
            metadata = {"version": 1, "saved_at": time.time(), "reason": reason,
                        "session": b, "transcript": transcript_meta, "recovery_directories": recovery,
                        "recovery_limitations": [] if transcript_meta else ["Provider transcript unavailable; only retained terminal scrollback was saved."],
                        "terminal_history": "All retained tmux scrollback; older lines may have been evicted."}
            _write(pending / "metadata.json", json.dumps(metadata, indent=2) + "\n")
            # Finish the project handoff while failure still prevents shutdown.
            hp = prompts.write_handoff(b["slug"], name, b["cwd"], screen, str(target))
            _write(pending / "handoff.md", Path(hp).read_text(encoding="utf-8"))
            os.rename(pending, target)
            store.update(name, archive_path=str(target))
            return str(target)
        except Exception:
            shutil.rmtree(pending, ignore_errors=True)
            raise


def archive_read(name):
    b = store.get(name)
    if not b or not b.get("archive_path"):
        return None
    path = Path(b["archive_path"]).resolve()
    if not path.is_relative_to(Path(config.path("history")).resolve()):
        raise RuntimeError("invalid archive path")
    try:
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        return {"metadata": metadata,
                "screen": (path / "terminal.txt").read_text(encoding="utf-8"),
                "handoff": (path / "handoff.md").read_text(encoding="utf-8"),
                "transcript_available": (path / _transcript_filename(metadata)).is_file()}
    except OSError as e:
        raise RuntimeError("session archive is unavailable") from e


def _restore_missing_file(source, destination):
    """Publish atomically without replacement; a newer live file always wins."""
    source, destination = Path(source), Path(destination)
    if destination.exists():
        return False
    if source.is_symlink():
        raise RuntimeError("invalid archived recovery symlink")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = destination.parent / (".blades-restore-" + uuid.uuid4().hex)
    try:
        with source.open("rb") as src, temp.open("xb") as dst:
            os.chmod(temp, 0o600)
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        try:
            os.link(temp, destination)
        except FileExistsError:
            return False
        return True
    finally:
        temp.unlink(missing_ok=True)


def _restore_missing_directory(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_dir():
        return
    if destination.is_symlink():
        raise RuntimeError("provider recovery destination is a symlink")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    for entry in source.iterdir():
        if entry.is_symlink():
            raise RuntimeError("invalid archived recovery symlink")
        target = destination / entry.name
        if entry.is_dir():
            _restore_missing_directory(entry, target)
        elif entry.is_file():
            _restore_missing_file(entry, target)


def restore_transcript(name):
    """Restore archived recovery data after provider retention purges it.

    Existing provider files are never overwritten. Returns the original
    transcript path for the resumed session, or raises when recovery cannot be
    established. Standalone JSONL and Gemini session JSON are supported;
    unrecognized storage formats require the provider's own resume capability.
    """
    with store.blade_lock(name):
        b = store.get(name)
        if not b:
            raise RuntimeError("unknown session")
        live_source = b.get("transcript_path")
        if not b.get("archive_path"):
            if live_source and _allowed_transcript(live_source, b["provider"]) and Path(live_source).is_file():
                if b["provider"] == "gemini":
                    _validate_gemini_transcript(live_source, b)
                return live_source
            raise RuntimeError("no provider transcript archive is available for this session")
        archive = archive_read(name)
        metadata = archive["metadata"]
        transcript = metadata.get("transcript")
        if not transcript:
            raise RuntimeError("this archive contains terminal history only; provider resume is unavailable")
        source = transcript["source"]
        if not _allowed_transcript(source, b["provider"]):
            raise RuntimeError("archived transcript destination is outside the provider directory")
        archived_path = Path(b["archive_path"])
        archive_file = archived_path / _transcript_filename(metadata)
        if b["provider"] == "gemini":
            _validate_gemini_transcript(archive_file, b)
            if Path(source).exists():
                _validate_gemini_transcript(source, b)
        _restore_missing_file(archive_file, source)
        if b["provider"] == "claude":
            sid = metadata["session"].get("session_id")
            if sid and Path(sid).name == sid and sid not in (".", ".."):
                _restore_missing_directory(archived_path / "session-files", Path(source).parent / sid)
                base = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
                _restore_missing_directory(archived_path / "file-history", base / "file-history" / sid)
        return source


def snapshot_unchanged(path):
    metadata = json.loads(Path(path, "metadata.json").read_text(encoding="utf-8"))
    transcript = metadata.get("transcript")
    if transcript:
        try:
            stat = os.stat(transcript["source"])
            return (stat.st_size, stat.st_mtime_ns) == (transcript["size"], transcript["mtime_ns"])
        except OSError:
            return False
    return True


def sweep(now=None):
    """One idle sweep. Safe for a periodic worker; it never blocks for exit."""
    from . import winddown
    now = time.time() if now is None else now
    settings = config.settings()
    minutes = max(0, float(settings.get("idle_timeout_min", 1440)))
    grace = max(0, float(settings.get("idle_grace_sec", 120)))
    protected = config.protected()
    started = []
    for row in store.live():
        name = row["name"]
        with store.blade_lock(name):
            b = store.get(name)
            if not b or winddown.is_active(name):
                continue
            last = max(b.get("last_input_at") or b["created_at"],
                       b.get("last_activity_at") or b["updated_at"])
            if not minutes or name in protected or not proven_idle(b) or now - last < minutes * 60:
                if b.get("idle_deadline") is not None:
                    store.update(name, idle_deadline=None)
                continue
            if b.get("idle_deadline") is None:
                store.update(name, idle_deadline=now + grace)
                continue
            if now >= b["idle_deadline"]:
                if winddown.start(name, None, automatic=True):
                    started.append(name)
    return started
