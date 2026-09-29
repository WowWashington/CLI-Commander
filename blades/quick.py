"""Quick actions: shell commands run from Settings, outside Claude.

Two modes:
  fire-and-forget (default) — runs detached, stdout/stderr to
      ~/.cli_blades/quick/<id>-<timestamp>.log; the UI shows the tail.
  keep open — runs inside a tmux blade under the pseudo-project
      "Shell Commands" so it appears in the left panel like any CLI.
Definitions live in ~/.cli_blades/quick_actions.json.
"""
import glob
import os
import shlex
import subprocess
import time

from . import config, store, tmuxctl

SLUG = "shell-commands"
PROJECT = "Shell Commands"
QUICK_DIR = config.path("quick")
os.makedirs(QUICK_DIR, exist_ok=True)

DEFAULT_ACTIONS = []


def actions():
    acts = config.load_json(config.path("quick_actions.json"), None)
    if acts is None:
        acts = DEFAULT_ACTIONS
        config.save_json(config.path("quick_actions.json"), acts)
    return acts


def save_actions(acts):
    clean = []
    for a in acts:
        if not a.get("id") or not a.get("command"):
            continue
        clean.append({"id": config.slugify(a["id"]), "label": a.get("label") or a["id"],
                      "command": a["command"], "cwd": a.get("cwd") or "~",
                      "keep_open": bool(a.get("keep_open"))})
    config.save_json(config.path("quick_actions.json"), clean)
    return clean


def get(aid):
    for a in actions():
        if a["id"] == aid:
            return a
    return None


def pseudo_project():
    """Synthetic project row so keep-open runs show under "Shell Commands"."""
    return {"num": 0, "name": PROJECT, "slug": SLUG, "keywords": "shell commands, quick actions",
            "path": config.HOME, "github_note": "", "status": "Active", "exists": True,
            "hidden": False, "git": None, "state_md": None, "handoff": False, "has_config": False,
            "status_line": "keep-open quick actions from Settings", "modified": None, "pseudo": True}


def run(aid, keep_open=None):
    a = get(aid)
    if not a:
        raise RuntimeError(f"unknown quick action {aid}")
    keep = a["keep_open"] if keep_open is None else bool(keep_open)
    cwd = os.path.expanduser(a.get("cwd") or "~")
    if not os.path.isdir(cwd):
        raise RuntimeError(f"cwd does not exist: {cwd}")
    ts = time.strftime("%Y%m%d-%H%M%S")
    if keep:
        name = tmuxctl.next_name(SLUG)
        store.create(name, SLUG, PROJECT, cwd, provider="shell")
        script = os.path.join(config.path("run"), f"{name}.sh")
        with open(script, "w", encoding="utf-8") as f:
            f.write(f"cd {shlex.quote(cwd)}\n"
                    f"printf '%s\\n' {shlex.quote('[quick] ' + a['label'])} {shlex.quote('$ ' + a['command'])} '';\n"
                    f"{a['command']}\n"
                    f"echo; echo \"[quick] exit $? — this window stays open; close it from the dashboard\"\n"
                    f"exec $SHELL -l\n")
        os.chmod(script, 0o755)
        s = config.settings()
        tmuxctl._run(["new-session", "-d", "-s", name, "-c", cwd,
                      "-x", str(s["tmux_cols"]), "-y", str(s["tmux_rows"]),
                      "/bin/zsh", "-lic", f"source {shlex.quote(script)}"], check=True)
        tmuxctl._run(["set-option", "-t", name, "status", "off"])
        store.set_state(name, "idle", note=f"quick: {a['label']}")
        return {"mode": "keep_open", "blade": name}
    log = os.path.join(QUICK_DIR, f"{a['id']}-{ts}.log")
    with open(log, "w", encoding="utf-8") as lf:
        lf.write(f"$ {a['command']}\n(cwd {cwd}, started {ts})\n\n")
        lf.flush()
        proc = subprocess.Popen(["/bin/zsh", "-lc", a["command"]], cwd=cwd, stdout=lf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    return {"mode": "background", "pid": proc.pid, "log": log}


def last_log(aid, lines=40):
    logs = sorted(glob.glob(os.path.join(QUICK_DIR, f"{aid}-*.log")), reverse=True)
    if not logs:
        return {"log": None, "text": ""}
    p = logs[0]
    try:
        text = open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        text = ""
    tail = "\n".join(text.splitlines()[-lines:])
    running = False
    return {"log": os.path.basename(p), "text": tail, "modified": time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(p)))}
