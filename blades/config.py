"""Paths, defaults, and user-config load/save for CLI Commander.

User configuration lives in ~/.cli_blades/ (never in the repo):
  layout.json            project order / hidden list
  defaults.json          global prompt template + settings
  commands.json          named commands (save state, wind down, ...)
  projects/<slug>.json   per-project prompt override, notes, related projects
  handoff/<slug>.md      handoff note written at wind-down, consumed on next open
  run/                   per-blade launcher scripts and prompt files
  blades.db              sqlite: blades, events
"""
import json
import ipaddress
import tempfile
import os
import re

HOME = os.path.expanduser("~")
CONFIG_DIR = os.environ.get("BLADES_HOME", os.path.join(HOME, ".cli_blades"))
PROJECTS_MD = os.environ.get("BLADES_PROJECTS_MD", os.path.join(HOME, "projects.md"))
def server_settings():
    try:
        with open(os.path.join(CONFIG_DIR, "server.json"), encoding="utf-8") as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def instance_name():
    """Optional label for this installation (e.g. "CLI Dev"), shown in the header and tab title."""
    value = os.environ.get("BLADES_INSTANCE_NAME") or server_settings().get("instance_name") or ""
    return value.strip()[:40] if isinstance(value, str) else ""


def valid_port(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 65535:
        raise ValueError("Port must be a whole number between 1024 and 65535")
    return value


PORT = valid_port(int(os.environ["BLADES_PORT"]) if "BLADES_PORT" in os.environ
                  else server_settings().get("port", 5090))
# Reachability policy: loopback + this device's Tailscale address, never LAN/wildcard.
HOST = os.environ.get("BLADES_HOST", "")


def tailscale_cli_environment():
    # The macOS app binary enters GUI mode without TERM (including launchd).
    # Set it only for this child so status/ip remain read-only CLI operations.
    env = os.environ.copy()
    env.setdefault("TERM", "dumb")
    return env


def tailscale_ip():
    """This node's Tailscale IPv4 (the 100.64/10 address on a utun), or ''."""
    import subprocess, re
    for cmd in (["tailscale", "ip", "-4"],
                ["/Applications/Tailscale.app/Contents/MacOS/Tailscale", "ip", "-4"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5,
                                 env=tailscale_cli_environment()).stdout.strip()
            candidate = out.split()[0] if out else ""
            if candidate and ipaddress.ip_address(candidate) in ipaddress.ip_network("100.64.0.0/10"):
                return candidate
        except Exception:
            pass
    # Fail closed when Tailscale cannot identify itself. Other VPNs and even
    # LANs may use CGNAT addresses; interface address shape is not proof.
    return ""


def bind_addresses():
    """Addresses the server binds. Default: 127.0.0.1 plus the Tailscale IP."""
    if HOST:
        if HOST not in ("127.0.0.1", "::1", "localhost", tailscale_ip()):
            raise ValueError("BLADES_HOST must be loopback or this device's Tailscale IP; LAN and wildcard binds are disabled")
        return [HOST]
    addrs = ["127.0.0.1"]
    ts = tailscale_ip()
    if ts:
        addrs.append(ts)
    return addrs
CLAUDE_BIN = os.environ.get("BLADES_CLAUDE_BIN", "claude")
CODEX_BIN = os.environ.get("BLADES_CODEX_BIN", "codex")
GEMINI_BIN = os.environ.get("BLADES_GEMINI_BIN", "gemini")

os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
os.chmod(CONFIG_DIR, 0o700)
for sub in ("projects", "handoff", "handoff/archive", "run"):
    folder = os.path.join(CONFIG_DIR, sub)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)

DEFAULT_PROMPT = """You are starting a fresh CLI Commander session for the project "{name}" at {path}.

First read STATE.md (if present) to load context. Do not review previous chat history.
{notes}{related}{handoff}
When you have read it, give a short summary (3 lines max) of where the project stands and what is next, then wait for instructions."""

DEFAULT_COMMANDS = [
    {
        "id": "save-state",
        "label": "Save state",
        "close": False,
        "text": (
            "Checkpoint request (do not exit): record this session's progress, "
            "unfinished work and exact next steps in STATE.md. Preserve existing "
            "edits. Do not commit, push, reset, stash, or modify unrelated work. "
            "If nothing needs saving, reply CHECKPOINT-CLEAN and stop."
        ),
    },
    {
        "id": "wind-down",
        "label": "Wind down & close",
        "close": True,
        "text": (
            "Wind-down request: safely park this session's work. Record progress, "
            "unfinished work and exact next steps in STATE.md. Do not commit, "
            "push, reset, stash, or alter another session's edits. Reply with "
            "loose ends (or NONE) and stop. Do not start new work."
        ),
    },
]

LEGACY_COMMANDS = [{'id': 'save-state', 'label': 'Save state', 'close': False, 'text': 'Checkpoint request (not a wind-down, do not exit): if this session has uncommitted work, run /state-update for this project and commit + push now. If work is mid-flight, do not finish it; record exactly where it stands in STATE.md, then commit. If there is nothing to save, reply CHECKPOINT-CLEAN and stop.'}, {'id': 'wind-down', 'label': 'Wind down & close', 'close': True, 'text': 'Wind-down request: this session is about to end. 1) Finish or safely park any in-flight edit so the working tree is coherent. 2) Run /state-update and record in STATE.md anything unfinished, unpublished, or not yet committed/pushed, with the exact next step for each. 3) Commit and push if a remote exists. 4) Reply with a bullet list of loose ends (or NONE) and stop. Do not start new work.'}]

DEFAULT_SETTINGS = {
    "prompt_template": DEFAULT_PROMPT,
    "winddown_timeout_sec": 900,
    "exit_timeout_sec": 90,
    "tmux_cols": 200,
    "tmux_rows": 50,
    "idle_timeout_min": 1440,
    "idle_grace_sec": 120,
}


def path(*parts):
    return os.path.join(CONFIG_DIR, *parts)


def load_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(p, data):
    fd, tmp = tempfile.mkstemp(prefix=".blades-", dir=os.path.dirname(p))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


LEGACY_PROMPTS = (DEFAULT_PROMPT.replace("CLI Commander", "CLI Blades"),)


def settings():
    s = dict(DEFAULT_SETTINGS)
    saved = load_json(path("defaults.json"), {})
    if saved.get("prompt_template") in LEGACY_PROMPTS:
        # An unmodified pre-rename default: follow the current default instead.
        saved = {k: v for k, v in saved.items() if k != "prompt_template"}
        save_json(path("defaults.json"), saved)
    s.update(saved)
    return s


def save_settings(data):
    save_json(path("defaults.json"), data)


def layout():
    return load_json(path("layout.json"), {"order": [], "hidden": []})


def save_layout(data):
    save_json(path("layout.json"), {"order": list(data.get("order", [])),
                                    "hidden": list(data.get("hidden", []))})


def commands():
    cmds = load_json(path("commands.json"), None)
    if cmds is None:
        cmds = DEFAULT_COMMANDS
        save_json(path("commands.json"), cmds)
    legacy_by_id = {c["id"]: c for c in LEGACY_COMMANDS}
    defaults_by_id = {c["id"]: c for c in DEFAULT_COMMANDS}
    updated = [defaults_by_id[c["id"]] if c == legacy_by_id.get(c.get("id")) else c for c in cmds]
    if updated != cmds:
        backup = path("commands.before-safe-checkpoints.json")
        if not os.path.exists(backup):
            save_json(backup, cmds)
        save_json(path("commands.json"), updated)
    return updated


def save_commands(cmds):
    save_json(path("commands.json"), cmds)


def project_config(slug):
    return load_json(path("projects", f"{slug}.json"),
                     {"prompt": "", "notes": "", "related": []})


def save_project_config(slug, cfg):
    save_json(path("projects", f"{slug}.json"), {
        "prompt": cfg.get("prompt", "") or "",
        "notes": cfg.get("notes", "") or "",
        "related": [r for r in cfg.get("related", []) if r],
    })


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "project"


def protected():
    """Blade names / terminal ttys excluded from batch commands and nightly."""
    return set(load_json(path("protected.json"), []))


def save_protected(items):
    save_json(path("protected.json"), sorted(set(items)))
