"""Flask app: JSON API, hook receiver, and websocket terminal bridge."""
import fcntl
import codecs
import hashlib
import re
import json
import logging
import os
import struct
import termios
import threading
import time

from flask import Flask, jsonify, request, abort
from flask_sock import Sock
from ptyprocess import PtyProcess

from . import audit, config, legacy, projects, prompts, quick, store, tmuxctl, winddown, security, providers, lifecycle, server_config

log = logging.getLogger("blades")
STATIC = os.path.join(os.path.dirname(__file__), "static")

app = Flask(__name__, static_folder=STATIC, static_url_path="/static")
security.install(app)
sock = Sock(app)
_open_lock = threading.Lock()
_reconcile_lock = threading.Lock()
_handoff_receipts = {}


# ---- helpers ---------------------------------------------------------------

def _ui_assets():
    """Version the served UI by content, including updates to an existing install."""
    digest = hashlib.sha256()
    index_html = ""
    for filename in ("index.html", "app.js", "app.css"):
        with open(os.path.join(STATIC, filename), "rb") as source:
            content = source.read()
        digest.update(filename.encode("ascii") + b"\0" + content + b"\0")
        if filename == "index.html":
            index_html = content.decode("utf-8")
    return digest.hexdigest()[:16], index_html


def _reconcile():
    """Keep ended panes long enough to archive; never overwrite a saved blade."""
    if not _reconcile_lock.acquire(blocking=False):
        return
    try:
        live_tmux = set(tmuxctl.list_sessions())
        for blade in store.live():
            name = blade["name"]
            if name not in live_tmux and not winddown.is_active(name):
                store.set_state(name, "closed", note="process ended; provider history retained")
        for name in live_tmux:
            blade = store.get(name)
            if blade and not winddown.is_active(name) and tmuxctl.pane_dead(name):
                try:
                    lifecycle.snapshot(name, reason="process-exited")
                except Exception:
                    log.exception("Archive failed; retaining ended pane %s", name)
                    continue
                tmuxctl.kill(name)
                store.set_state(name, "closed", note="saved after process exit")
            elif not blade:
                slug = name[len(tmuxctl.PREFIX):].rsplit("-", 1)[0]
                project = projects.by_slug(slug)
                if project:
                    store.create(name, slug, project["name"], project["path"])
                    store.set_state(name, "unknown", note="reconnected; awaiting provider activity")
    finally:
        _reconcile_lock.release()


def _recover_native_transcript(b):
    """Backfill exact IDs from pre-archive releases without guessing 'latest'."""
    if b.get("session_id") and not b.get("transcript_path"):
        source = providers.transcript(b.get("provider", "claude"), b["session_id"])
        if source:
            store.update(b["name"], transcript_path=source)
            b = dict(b, transcript_path=source)
    return b


def _blade_view(b):
    b = _recover_native_transcript(b)
    d = dict(b)
    d["protected"] = b["name"] in config.protected()
    d["age_sec"] = max(0, int(time.time() - b["created_at"]))
    idle = lifecycle.idle_status(b)
    d.update(idle)
    d["idle_remaining_sec"] = idle.get("close_in_sec")
    d["idle_pending"] = bool(b.get("idle_deadline"))
    d["winddown"] = winddown.is_active(b["name"])
    d["archive_available"] = bool(b.get("archive_path") and os.path.isdir(b["archive_path"]))
    archive_has_transcript = any(os.path.isfile(os.path.join(b.get("archive_path") or "", file))
                                 for file in ("transcript.jsonl", "transcript.json")) if b.get("archive_path") else False
    native_exists = bool(b.get("transcript_path") and os.path.isfile(b["transcript_path"]))
    d["resumable"] = bool(b.get("session_id") and b.get("provider", "claude") in ("claude", "codex", "gemini")
                          and (native_exists or archive_has_transcript))
    return d


def _project_view(p):
    live = [b for b in store.live() if b["slug"] == p["slug"]]
    p = dict(p)
    p["blades"] = [b["name"] for b in live]
    p["attention"] = any(b["state"] == "attention" for b in live)
    return p


def _open_blade(slug, shell_only=False, provider="claude", resume_id=None):
    project = projects.by_slug(slug)
    if not project:
        abort(404, "unknown project")
    if not project["exists"]:
        abort(400, "project path does not exist")
    if provider not in providers.PROVIDER_IDS:
        abort(400, "unknown provider")
    if not shell_only and not resume_id:
        from . import onboarding
        if not onboarding.enabled(provider):
            abort(409, "Enable this assistant in Settings → Assistants & setup first")
    if not shell_only and not providers.executable(provider):
        abort(400, f"{provider} CLI is not installed")
    if not shell_only and provider == "ollama":
        from . import onboarding, ollama
        if resume_id:
            abort(409, "Ollama exact conversation resume is not supported")
        check = ollama.status(providers.executable(provider), onboarding.preferences()['ollama_model'])
        if check['state'] != 'ready':
            abort(409, check['detail'])
    with _open_lock:
        if server_config.status()["restart_required"] and not os.environ.get("BLADES_PORT"):
            abort(409, "Restart CLI Commander on the saved port before opening another session")
        if resume_id and any(b.get("session_id") == resume_id for b in store.live()):
            abort(409, "This conversation is already open in another blade")
        name = tmuxctl.next_name(slug)
        text = "" if shell_only or resume_id or provider == "ollama" else prompts.render(project)
        store.create(name, slug, project["name"], project["path"], provider="shell" if shell_only else provider)
        if resume_id:
            store.set_state(name, "starting", session_id=resume_id)
        if text:
            try:
                with open(prompts.handoff_path(slug), "rb") as f:
                    _handoff_receipts[name] = hashlib.sha256(f.read()).hexdigest()
            except FileNotFoundError:
                pass
        try:
            tmuxctl.new_session(name, project["path"], text, shell_only=shell_only,
                                provider=provider, resume_id=resume_id)
        except Exception as e:
            _handoff_receipts.pop(name, None)
            store.set_state(name, "closed", note=f"launch failed: {e}")
            abort(500, str(e))
        if shell_only:
            store.set_state(name, "idle", note="shell; automatic close disabled")
        elif provider == "ollama":
            store.set_state(name, "unknown", note="Ollama chat: completion unknown; manual close only; no exact resume")
        return store.get(name)


@app.before_request
def validate_targets():
    values = request.view_args or {}
    name = values.get("name")
    if name is not None and (not re.fullmatch(r"blade-[a-z0-9-]+", name) or not store.get(name)):
        abort(404, "unknown blade")
    slug = values.get("slug")
    if slug is not None and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        abort(400, "invalid project slug")


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(413)
@app.errorhandler(415)
def api_error(error):
    return jsonify(error=error.description), error.code


@app.get("/api/providers")
def api_providers():
    return jsonify(providers.available())


@app.get("/api/onboarding")
def api_onboarding():
    from . import onboarding
    return jsonify(onboarding.status(refresh=request.args.get("refresh") == "1"))


@app.post("/api/onboarding")
def api_onboarding_save():
    from . import onboarding
    try:
        return jsonify(onboarding.configure(request.get_json() or {}))
    except ValueError as exc:
        abort(400, str(exc))


@app.post("/api/onboarding/reset")
def api_onboarding_reset():
    from . import onboarding
    return jsonify(onboarding.reset())


@app.get("/api/server-config")
def api_server_config():
    return jsonify(server_config.status())


@app.post("/api/server-config")
def api_server_config_save():
    try:
        with _open_lock:
            return jsonify(server_config.configure(request.get_json() or {}))
    except ValueError as exc:
        abort(400, str(exc))
    except RuntimeError as exc:
        abort(409, str(exc))


@app.get("/api/status")
def api_status():
    settings = config.settings()
    return {"access": "Local + Tailscale only", "lan_access": False,
            "origin_protection": True, "bind_addresses": config.bind_addresses(),
            "tailscale": bool(config.tailscale_ip()), "providers": providers.available(),
            "ui_version": _ui_assets()[0],
            "idle_timeout_min": settings["idle_timeout_min"], "idle_grace_sec": settings["idle_grace_sec"]}


# ---- pages -----------------------------------------------------------------

@app.get("/")
def index():
    version, html = _ui_assets()
    return html.replace("__UI_VERSION__", version)


@app.get("/healthz")
def healthz():
    return {"ok": True, "tmux": bool(tmuxctl.list_sessions()) or True}


# ---- projects --------------------------------------------------------------

@app.get("/api/projects")
def api_projects():
    _reconcile()
    rows = [_project_view(p) for p in projects.all_projects()]
    if any(b["slug"] == quick.SLUG for b in store.live()):
        rows.insert(0, _project_view(quick.pseudo_project()))
    return jsonify(rows)


@app.post("/api/projects")
def api_project_add():
    body = request.get_json() or {}
    if not isinstance(body, dict):
        abort(400, "Project must be an object")
    try:
        return jsonify(projects.add_project(body.get("path"), body.get("name")))
    except ValueError as exc:
        abort(400, str(exc))


@app.put("/api/layout")
def api_layout():
    config.save_layout(request.get_json(force=True) or {})
    return {"ok": True}


@app.get("/api/projects/<slug>/config")
def api_project_config(slug):
    cfg = config.project_config(slug)
    p = projects.by_slug(slug)
    cfg["preview"] = prompts.render(p) if p else ""
    cfg["handoff"] = prompts.read_handoff(slug)
    cfg["default_template"] = config.settings()["prompt_template"]
    return jsonify(cfg)


@app.put("/api/projects/<slug>/config")
def api_project_config_put(slug):
    config.save_project_config(slug, request.get_json(force=True) or {})
    return {"ok": True}


@app.delete("/api/projects/<slug>/handoff")
def api_handoff_clear(slug):
    prompts.consume_handoff(slug)
    return {"ok": True}


@app.get("/api/settings")
def api_settings():
    return jsonify(config.settings())


@app.put("/api/settings")
def api_settings_put():
    s = config.settings()
    body = request.get_json(force=True) or {}
    if not isinstance(body, dict):
        abort(400, "settings must be an object")
    ranges = {"idle_timeout_min": (0, 10080), "idle_grace_sec": (30, 3600),
              "winddown_timeout_sec": (10, 3600), "exit_timeout_sec": (1, 300),
              "tmux_cols": (20, 500), "tmux_rows": (5, 300)}
    for key, value in body.items():
        if key not in config.DEFAULT_SETTINGS:
            abort(400, "unknown setting")
        if key in ranges:
            low, high = ranges[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                abort(400, f"{key} must be between {low} and {high}")
        elif not isinstance(value, str):
            abort(400, f"{key} must be text")
    s.update(body)
    config.save_settings(s)
    return {"ok": True}


# ---- blades ----------------------------------------------------------------

@app.get("/api/blades")
def api_blades():
    _reconcile()
    return jsonify({"live": [_blade_view(b) for b in store.live()],
                    "recent": [_blade_view(b) for b in store.recent_closed(50)]})


@app.post("/api/blades")
def api_blade_open():
    body = request.get_json(force=True) or {}
    b = _open_blade(body.get("slug", ""), shell_only=bool(body.get("shell")), provider=body.get("provider", "claude"))
    return jsonify(_blade_view(b))


@app.get("/api/blades/<name>")
def api_blade(name):
    b = store.get(name)
    if not b:
        abort(404)
    return jsonify(_blade_view(b))


@app.get("/api/blades/<name>/screen")
def api_blade_screen(name):
    try:
        lines = max(1, min(5000, int(request.args.get("lines", 60))))
    except ValueError:
        abort(400, "invalid line count")
    return {"text": tmuxctl.capture(name, lines)}


@app.post("/api/blades/<name>/send")
def api_blade_send(name):
    body = request.get_json(force=True) or {}
    text = body.get("text", "")
    key = body.get("key")
    try:
        with lifecycle.interaction(name):
            if key:
                if key not in ("Escape", "Tab", "C-c", "Up", "Down", "Left", "Right", "Enter", "BTab", "BSpace"):
                    abort(400, "unsupported key")
                tmuxctl.send_key(name, key)
            else:
                if not isinstance(text, str) or len(text) > 100000:
                    abort(400, "invalid text")
                tmuxctl.send_text(name, text, enter=body.get("enter", True))
    except RuntimeError as e:
        abort(409, str(e))
    return {"ok": True}


@app.post("/api/blades/<name>/winddown")
def api_blade_winddown(name):
    body = request.get_json(force=True) or {}
    cmd = _command(body.get("command", "wind-down"))
    blade = store.get(name)
    if blade["state"] != "idle":
        abort(409, "Wait until this session is idle before winding down")
    try:
        started = winddown.start(name, cmd["text"])
    except RuntimeError as e:
        abort(409, str(e))
    return {"ok": started, "already": not started}


@app.delete("/api/blades/<name>")
def api_blade_kill(name):
    try:
        lifecycle.snapshot(name, reason="manual-force-close")
    except Exception:
        log.exception("Archive failed for %s", name)
        abort(409, "Archive failed; session left running")
    tmuxctl.kill(name)
    store.set_state(name, "closed", note="manually closed; snapshot saved")
    return {"ok": True}


@app.post("/api/blades/<name>/resize")
def api_blade_resize(name):
    body = request.get_json(force=True) or {}
    try:
        cols, rows = int(body.get("cols", 120)), int(body.get("rows", 40))
        if not 20 <= cols <= 500 or not 5 <= rows <= 300:
            raise ValueError()
    except (ValueError, TypeError):
        abort(400, "invalid terminal dimensions")
    tmuxctl.resize_window(name, cols, rows)
    return {"ok": True}


@app.get("/api/blades/<name>/archive")
def api_archive(name):
    data = lifecycle.archive_read(name)
    if not data:
        abort(404, "No archive for this session")
    return jsonify(dict(data, text=data.get("handoff", "") + "\n\n" + data.get("screen", "")))


@app.post("/api/blades/<name>/resume")
def api_resume(name):
    blade = _recover_native_transcript(store.get(name))
    if blade["state"] != "closed":
        abort(409, "This session is already open")
    if not blade.get("session_id") or blade.get("provider") not in ("claude", "codex", "gemini"):
        abort(409, "No exact provider conversation ID saved; open a fresh session")
    if any(b.get("session_id") == blade["session_id"] for b in store.live()):
        abort(409, "This conversation is already open in another blade")
    try:
        lifecycle.restore_transcript(name)
    except RuntimeError as e:
        abort(409, str(e))
    return jsonify(_blade_view(_open_blade(blade["slug"], provider=blade["provider"], resume_id=blade["session_id"])))


@app.post("/api/blades/<name>/keepalive")
def api_keepalive(name):
    try:
        lifecycle.keepalive(name)
    except RuntimeError as e:
        abort(409, str(e))
    return {"ok": True}


# ---- commands --------------------------------------------------------------

def _command(cid):
    for c in config.commands():
        if c["id"] == cid:
            return c
    abort(404, f"unknown command {cid}")


@app.get("/api/commands")
def api_commands():
    return jsonify(config.commands())


@app.put("/api/commands")
def api_commands_put():
    cmds = request.get_json(force=True) or []
    config.save_commands([c for c in cmds if c.get("id") and c.get("text")])
    return {"ok": True}


def run_command(cmd: dict, targets="all", include_legacy=False) -> dict:
    """Send a command (text, optional close=wind-down) to blades and, optionally,
    Terminal.app windows. Protected targets are skipped. Returns per-target results."""
    live = store.live()
    if targets != "all" and not isinstance(targets, list):
        raise RuntimeError("targets must be 'all' or a list of blade names")
    requested = [b for b in live if targets == "all" or b["name"] in targets]
    names, results = [], {}
    for blade in requested:
        name = blade["name"]
        if blade["state"] != "idle" or blade.get("provider", "claude") in ("shell", "ollama") or winddown.is_active(name):
            results[name] = "not idle: skipped"
        else:
            names.append(name)
    prot = config.protected()
    for n in [n for n in names if n in prot]:
        results[n] = "protected: skipped"
    names = [n for n in names if n not in prot]
    if include_legacy:
        for ls in legacy.list_sessions():
            key = f"terminal:{ls['tty']} ({ls['project']})"
            if ls["tty"] in prot:
                results[key] = "protected: skipped"
                continue
            if ls.get("state") != "idle":
                results[key] = "not confirmed idle: skipped"
                continue
            try:
                if cmd.get("close"):
                    ok = legacy.start_migrate(ls["pid"], mode="fresh", text=cmd["text"],
                                              open_blade=False)
                    results[key] = "winddown" if ok else "already"
                else:
                    legacy.send(ls["tty"], cmd["text"])
                    results[key] = "sent"
            except RuntimeError as e:
                results[key] = f"error: {e}"
    for n in names:
        try:
            if cmd.get("close"):
                results[n] = "winddown" if winddown.start(n, cmd["text"]) else "already"
            else:
                with lifecycle.interaction(n):
                    tmuxctl.send_text(n, cmd["text"])
                results[n] = "sent"
        except RuntimeError as e:
            results[n] = f"error: {e}"
    return results


@app.post("/api/commands/<cid>/run")
def api_command_run(cid):
    cmd = _command(cid)
    body = request.get_json(force=True) or {}
    return jsonify(run_command(cmd, body.get("blades", "all"), bool(body.get("include_legacy"))))


@app.post("/api/broadcast")
def api_broadcast():
    """Settings > Broadcast: a named command or free text to every window."""
    body = request.get_json(force=True) or {}
    if body.get("command"):
        cmd = dict(_command(body["command"]))
    else:
        text = (body.get("text") or "").strip()
        if not text:
            abort(400, "nothing to send")
        cmd = {"id": "adhoc", "text": text, "close": bool(body.get("close"))}
    if "close" in body:
        cmd["close"] = bool(body["close"])
    res = run_command(cmd, body.get("blades", "all"), bool(body.get("include_legacy", False)))
    return jsonify({"command": cmd["id"], "close": cmd["close"], "results": res})


# ---- quick actions -----------------------------------------------------------

@app.get("/api/quick")
def api_quick():
    return jsonify(quick.actions())


@app.put("/api/quick")
def api_quick_put():
    return jsonify(quick.save_actions(request.get_json(force=True) or []))


@app.post("/api/quick/<aid>/run")
def api_quick_run(aid):
    body = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(quick.run(aid, body.get("keep_open")))
    except RuntimeError as e:
        abort(400, str(e))


@app.get("/api/quick/<aid>/log")
def api_quick_log(aid):
    return jsonify(quick.last_log(aid, int(request.args.get("lines", 40))))


# ---- hooks -----------------------------------------------------------------

@app.post("/hook")
def hook():
    body = request.get_json(force=True, silent=True) or {}
    if not isinstance(body, dict):
        abort(400, "hook body must be an object")
    blade, event = body.get("blade"), body.get("event")
    if not isinstance(blade, str) or not isinstance(event, str) or not blade or not event:
        return {"ok": False}, 400
    if not store.get(blade):
        _reconcile()
        if not store.get(blade):
            return {"ok": False, "unknown": True}
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        abort(400, "hook payload must be an object")
    if event.startswith("Gemini") and store.get(blade).get("provider") == "gemini":
        try:
            state = store.apply_gemini_hook(blade, event.removeprefix("Gemini"), payload)
        except RuntimeError as e:
            abort(409, str(e))
    elif event == "CodexTurnComplete" and store.get(blade).get("provider") == "codex":
        sid = payload.get("session_id")
        if not isinstance(sid, str) or not re.fullmatch(r"[a-zA-Z0-9-]{10,80}", sid):
            abort(400, "invalid session ID")
        current = store.get(blade)
        source = providers.codex_completion_transcript(sid, current["cwd"])
        if (current.get("session_id") and current["session_id"] != sid) or not source:
            store.record_event(blade, "IgnoredCodexCompletion", payload)
            return {"ok": True, "ignored": "not this top-level session", "state": current["state"]}
        store.record_event(blade, "Stop", payload)
        store.mark_activity(blade, "closing" if current["state"] == "closing" else "idle",
                            "codex:turn-complete", session_id=sid, transcript_path=source)
        state = store.get(blade)["state"]
    else:
        state = store.apply_hook(blade, event, payload)
    if event in ("SessionStart", "CodexTurnComplete", "GeminiSessionStart") and blade in _handoff_receipts:
        digest = _handoff_receipts.pop(blade)
        prompts.consume_handoff(store.get(blade)["slug"], expected_digest=digest)
    return {"ok": True, "state": state}


@app.post("/hook/legacy")
def hook_legacy():
    body = request.get_json(force=True, silent=True) or {}
    payload = body.get("payload") or {}
    sid = payload.get("session_id")
    if not sid:
        return {"ok": False}, 400
    legacy.apply_hook(sid, payload.get("cwd", ""), body.get("event"), payload)
    return {"ok": True}


# ---- legacy Terminal.app sessions -------------------------------------------

@app.get("/api/legacy")
def api_legacy():
    prot = config.protected()
    rows = legacy.list_sessions()
    for r in rows:
        r["protected"] = r["tty"] in prot
    return jsonify(rows)


@app.get("/api/protected")
def api_protected():
    return jsonify(sorted(config.protected()))


@app.post("/api/protected/toggle")
def api_protected_toggle():
    key = (request.get_json(force=True) or {}).get("key", "")
    prot = config.protected()
    if key in prot:
        prot.discard(key)
    else:
        prot.add(key)
    config.save_protected(prot)
    return {"ok": True, "protected": key in prot}


@app.get("/api/legacy/<int:pid>/candidates")
def api_legacy_candidates(pid):
    s = legacy.get(pid)
    if not s:
        abort(404)
    return jsonify(legacy.transcript_candidates(s["cwd"]))


@app.get("/api/legacy/<int:pid>/screen")
def api_legacy_screen(pid):
    s = legacy.get(pid)
    if not s:
        abort(404)
    return {"text": legacy.contents(s["tty"], int(request.args.get("lines", 60)))}


@app.post("/api/legacy/<int:pid>/send")
def api_legacy_send(pid):
    s = legacy.get(pid)
    if not s:
        abort(404)
    body = request.get_json(force=True) or {}
    text = body.get("text") or _command(body.get("command", "save-state"))["text"]
    legacy.send(s["tty"], text)
    return {"ok": True}


@app.post("/api/legacy/<int:pid>/migrate")
def api_legacy_migrate(pid):
    body = request.get_json(force=True) or {}
    mode = body.get("mode", "fresh")
    cmd = _command(body.get("command", "wind-down"))
    try:
        started = legacy.start_migrate(pid, mode=mode, text=cmd["text"],
                                       session_id=body.get("session_id"),
                                       open_blade=body.get("open_blade", True),
                                       target_slug=body.get("target_slug"))
    except RuntimeError as e:
        abort(409, str(e))
    return {"ok": started, "already": not started}


# ---- audit -------------------------------------------------------------------

@app.get("/api/audit")
def api_audit():
    return jsonify(audit.report())


@app.post("/api/maintenance/run")
def api_maintenance_run():
    _reconcile()
    return {"started": lifecycle.sweep()}


@app.post("/api/audit/run")
def api_audit_run():
    return jsonify(audit.write_report())


@app.post("/api/audit/register")
def api_audit_register():
    body = request.get_json(force=True) or {}
    try:
        return jsonify(audit.register(body.get("name", ""), body.get("keywords")))
    except RuntimeError as e:
        abort(400, str(e))


@app.post("/api/audit/ignore")
def api_audit_ignore():
    audit.add_ignore((request.get_json(force=True) or {}).get("name", ""))
    return {"ok": True}


# ---- websocket terminal ------------------------------------------------------

_TERMINAL_REPLY = re.compile(
    rb"(?:\x1b\[[?=>]?[0-9;:]*[cnRt]|\x1b\[[?=>]?[0-9;:]*\$y|"
    rb"\x1b\[[IO]|\x1b\](?:10|11|12);[^\x07\x1b]*(?:\x07|\x1b\\)|"
    rb"\x1bP[^\x1b]*\x1b\\)+")


def _is_terminal_reply(raw):
    return bool(_TERMINAL_REPLY.fullmatch(raw))


def _set_winsize(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


@sock.route("/ws/term/<name>")
def ws_term(ws, name):
    if store.get(name)["state"] == "closed" or not tmuxctl.exists(name):
        ws.send("\r\n\x1b[31m[blade] session not running\x1b[0m\r\n")
        return
    env = dict(os.environ, TERM="xterm-256color", LANG=os.environ.get("LANG", "en_US.UTF-8"))
    proc = PtyProcess.spawn(tmuxctl.command(["attach-session", "-t", f"={name}"]),
                            env=env, dimensions=(40, 120))
    alive = True
    lifecycle.attach(name)

    def reader():
        nonlocal alive
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while alive:
                data = proc.read(65536)
                if not data:
                    break
                ws.send(decoder.decode(data))
        except Exception:
            pass
        finally:
            alive = False
            try:
                ws.close()
            except Exception:
                pass

    threading.Thread(target=reader, daemon=True).start()
    try:
        while alive:
            msg = ws.receive()
            if msg is None:
                break
            if isinstance(msg, str) and msg.startswith("\x00"):
                try:
                    ctl = json.loads(msg[1:])
                    if ctl.get("type") == "resize":
                        rows, cols = int(ctl["rows"]), int(ctl["cols"])
                        if 5 <= rows <= 300 and 20 <= cols <= 500:
                            _set_winsize(proc.fd, rows, cols)
                            proc.setwinsize(rows, cols)
                except Exception:
                    pass
                continue
            # Terminal replies to queries aren't user input and must not reset idle.
            raw = msg.encode() if isinstance(msg, str) else msg
            if _is_terminal_reply(raw):
                proc.write(raw)
            else:
                with lifecycle.interaction(name):
                    proc.write(raw)
    except Exception:
        pass
    finally:
        alive = False
        lifecycle.detach(name)
        try:
            proc.terminate(force=True)
        except Exception:
            pass


def create_app():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    store.conn()
    config.commands()
    from . import install
    install.hooks_settings_path()
    _reconcile()
    for b in store.live():
        if b["state"] == "closing":
            store.set_state(b["name"], "unknown", note="wind-down interrupted; session preserved")
    return app
