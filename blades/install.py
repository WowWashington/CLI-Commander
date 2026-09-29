"""Installers: Claude Code hooks into ~/.claude/settings.json, launchd plists."""
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import time
from xml.parsers.expat import ExpatError

from . import config

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "blade-hook.sh")
SETTINGS = os.path.join(config.HOME, ".claude", "settings.json")
EVENTS = ["SessionStart", "UserPromptSubmit", "Stop", "Notification", "PermissionRequest",
          "PreToolUse", "PostToolUse", "PostToolUseFailure", "SubagentStart", "SubagentStop", "SessionEnd"]
LAUNCH_AGENTS = os.path.join(config.HOME, "Library", "LaunchAgents")
SERVICE_LABEL = "io.cli-blades.server"
NIGHTLY_LABEL = "io.cli-blades.nightly"
GEMINI_EVENTS = ["SessionStart", "BeforeAgent", "AfterAgent", "BeforeTool", "AfterTool",
                 "BeforeModel", "Notification", "SessionEnd"]


def gemini_launch_environment(name):
    """Private per-launch hook overlay; retain the original admin policy locations.

    Gemini resolves policies and system defaults beside its system settings file.
    Moving that file alone would silently omit the administrator's protections.
    Only generated files and the private policy symlink are written here.
    """
    if not name.startswith("blade-") or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in name):
        raise RuntimeError("invalid Gemini blade name")
    default_system = ("/Library/Application Support/GeminiCli/settings.json"
                      if sys.platform == "darwin" else "/etc/gemini-cli/settings.json")
    original = Path(os.environ.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH") or default_system).absolute()
    defaults = Path(os.environ.get("GEMINI_CLI_SYSTEM_DEFAULTS_PATH") or original.with_name("system-defaults.json")).absolute()
    try:
        with original.open(encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as exc:
        raise RuntimeError("Cannot safely preserve Gemini system settings; launch paused") from exc
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise RuntimeError("Gemini system settings must contain an object and a hooks object")
    hooks = data.setdefault("hooks", {})
    for event in GEMINI_EVENTS:
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise RuntimeError("Cannot safely merge Gemini system hooks")
        entries.append({"matcher": "", "hooks": [{
            "type": "command", "name": "cli-blades-" + event,
            "command": shlex.join([sys.executable, os.path.join(REPO, "hooks", "gemini-hook.py"), event]),
            "timeout": 4000,
        }]})
    folder = Path(config.path("run", name + ".gemini"))
    if folder.is_symlink():
        raise RuntimeError("Gemini launch settings directory cannot be a symlink")
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    policies = folder / "policies"
    original_policies = original.with_name("policies")
    if policies.is_symlink():
        if os.readlink(policies) != str(original_policies):
            raise RuntimeError("Gemini policy location changed; launch paused")
    elif policies.exists():
        raise RuntimeError("Gemini policy overlay must preserve the original policies")
    else:
        policies.symlink_to(original_policies, target_is_directory=True)
    settings = folder / "settings.json"
    config.save_json(str(settings), data)
    return {"GEMINI_CLI_SYSTEM_SETTINGS_PATH": str(settings),
            "GEMINI_CLI_SYSTEM_DEFAULTS_PATH": str(defaults)}


def hooks_settings_path():
    """Per-blade settings file passed via `claude --settings`; keeps the hook
    relay out of ~/.claude/settings.json. Rewritten on every server start."""
    data = {"hooks": {ev: [{"matcher": "", "hooks": [
        {"type": "command", "command": shlex.join([HOOK, ev]), "timeout": 5}]}] for ev in EVENTS}}
    p = os.path.join(config.CONFIG_DIR, "claude-hooks.json")
    config.save_json(p, data)
    return p


def install_hooks():
    try:
        data = json.load(open(SETTINGS, encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    os.makedirs(os.path.dirname(SETTINGS), mode=0o700, exist_ok=True)
    if os.path.exists(SETTINGS):
        shutil.copy(SETTINGS, SETTINGS + f".bak-blades-{time.strftime('%Y%m%d%H%M%S')}")
    hooks = data.setdefault("hooks", {})
    changed = []
    for ev in EVENTS:
        entries = hooks.setdefault(ev, [])
        already = any(HOOK in h.get("command", "")
                      for e in entries for h in e.get("hooks", []))
        if already:
            continue
        entries.append({"matcher": "", "hooks": [
            {"type": "command", "command": shlex.join([HOOK, ev]), "timeout": 5}]})
        changed.append(ev)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, SETTINGS)
    return changed


def uninstall_hooks():
    data = json.load(open(SETTINGS, encoding="utf-8"))
    hooks = data.get("hooks", {})
    for ev in EVENTS:
        hooks[ev] = [e for e in hooks.get(ev, [])
                     if not any(HOOK in h.get("command", "") for h in e.get("hooks", []))]
        if not hooks[ev]:
            hooks.pop(ev)
    json.dump(data, open(SETTINGS, "w", encoding="utf-8"), indent=2)


def _plist(label, args, extra):
    d = {
        "Label": label,
        "ProgramArguments": args,
        "WorkingDirectory": REPO,
        "StandardOutPath": os.path.join(config.CONFIG_DIR, f"{label}.log"),
        "StandardErrorPath": os.path.join(config.CONFIG_DIR, f"{label}.log"),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": config.HOME,
            "BLADES_HOME": config.CONFIG_DIR,
            **{key: os.environ[key] for key in ("BLADES_PROJECTS_MD", "BLADES_PROJECTS_ROOT", "BLADES_HOST",
                "BLADES_CLAUDE_BIN", "BLADES_CODEX_BIN", "BLADES_GEMINI_BIN", "CODEX_HOME",
                "CLAUDE_CONFIG_DIR", "GEMINI_CLI_HOME", "GEMINI_CLI_SYSTEM_SETTINGS_PATH",
                "GEMINI_CLI_SYSTEM_DEFAULTS_PATH") if key in os.environ},
        },
    }
    d.update(extra)
    return d


def _owns_service(data):
    if not isinstance(data, dict):
        return False
    args, env = data.get("ProgramArguments"), data.get("EnvironmentVariables", {})
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args) or not isinstance(env, dict):
        return False
    folder = data.get("WorkingDirectory")
    home = env.get("HOME") or config.HOME
    if not isinstance(home, str):
        return False
    state = env.get("BLADES_HOME") or os.path.join(home, ".cli_blades")
    return (isinstance(folder, str) and isinstance(state, str)
            and os.path.realpath(folder) == os.path.realpath(REPO)
            and os.path.realpath(os.path.expanduser(state)) == os.path.realpath(config.CONFIG_DIR)
            and len(args) >= 4 and args[-3:-1] == ["-m", "blades"]
            and args[-1] in ("serve", "nightly"))


def _read_plist(path):
    try:
        with path.open("rb") as stream:
            return plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ExpatError, ValueError):
        return None


def install_launchd(nightly_hour=22, nightly_minute=30):
    if sys.platform != "darwin":
        raise RuntimeError("Automatic service installation currently supports macOS; run `python -m blades serve` on other hosts")
    py = os.path.join(REPO, ".venv", "bin", "python")
    os.makedirs(LAUNCH_AGENTS, exist_ok=True)
    # Refuse to overwrite another checkout/instance's generic service labels.
    for label in (SERVICE_LABEL, NIGHTLY_LABEL):
        destination = Path(LAUNCH_AGENTS) / (label + ".plist")
        if destination.exists() and not _owns_service(_read_plist(destination)):
            raise RuntimeError("The CLI Commander service label belongs to another checkout or data directory; installation paused")
    # Preserve the effective port, including an explicit environment override.
    # Changing it while running CLIs exist would disconnect their hook relay.
    saved = config.server_settings()
    if saved.get("port", config.PORT) != config.PORT:
        from . import tmuxctl
        if tmuxctl.list_sessions():
            raise RuntimeError("Saved and effective ports differ; close live sessions before reinstalling the service")
    config.save_json(config.path("server.json"), {**saved, "port": config.PORT})
    # Migrate only a service that launches this checkout; never touch another app.
    for old in Path(LAUNCH_AGENTS).glob("*.plist"):
        if old.stem in (SERVICE_LABEL, NIGHTLY_LABEL):
            continue
        if not _owns_service(_read_plist(old)):
            continue
        backup = Path(config.path("backups", "service-migration"))
        backup.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = backup / (old.name + "." + str(time.time_ns()))
        shutil.copy2(old, destination)
        destination.chmod(0o600)
        result = subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(old)], capture_output=True)
        if result.returncode not in (0, 3):
            raise RuntimeError("Could not stop the previous service; installation paused to avoid duplicate listeners")
        old.unlink()
    out = []
    svc = _plist(SERVICE_LABEL, [py, "-m", "blades", "serve"],
                 {"RunAtLoad": True, "KeepAlive": True})
    nightly = _plist(NIGHTLY_LABEL, [py, "-m", "blades", "nightly"],
                     {"StartCalendarInterval": {"Hour": nightly_hour, "Minute": nightly_minute}})
    for label, d in ((SERVICE_LABEL, svc), (NIGHTLY_LABEL, nightly)):
        p = os.path.join(LAUNCH_AGENTS, f"{label}.plist")
        with open(p, "wb") as f:
            plistlib.dump(d, f)
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", p],
                       capture_output=True)
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", p],
                           capture_output=True, text=True)
        out.append((label, r.returncode, r.stderr.strip()))
    return out
