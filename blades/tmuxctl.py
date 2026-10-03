"""Thin tmux wrapper. One tmux session per blade, named blade-<slug>-<n>."""
import os
import json
import sys
import threading
import shlex
import subprocess
import time

from . import config

PREFIX = "blade-"


def command(args):
    socket = os.environ.get("BLADES_TMUX_SOCKET")
    return ["tmux", *(["-L", socket] if socket else []), *args]


def _run(args, check=False, timeout=10):
    r = subprocess.run(command(args), capture_output=True, text=True,
                       timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"tmux {' '.join(args)} failed")
    return r


def list_sessions():
    r = _run(["list-sessions", "-F", "#{session_name}"])
    if r.returncode != 0:
        return []
    return [s for s in r.stdout.split("\n") if s.startswith(PREFIX)]


def exists(name):
    return _run(["has-session", "-t", f"={name}"]).returncode == 0


def next_name(slug):
    from . import store
    live = set(list_sessions())
    n = 1
    while f"{PREFIX}{slug}-{n}" in live or store.get(f"{PREFIX}{slug}-{n}"):
        n += 1
    return f"{PREFIX}{slug}-{n}"


def new_session(name, cwd, prompt_text, shell_only=False, resume_id=None, provider="claude"):
    """Launch a local provider without changing its saved account/configuration."""
    from . import install, providers
    s = config.settings()
    prompt_file = config.path("run", f"{name}.prompt")
    script = config.path("run", f"{name}.sh")
    with open(prompt_file, "w", encoding="utf-8") as f:
        f.write(prompt_text)
    os.chmod(prompt_file, 0o600)
    if shell_only:
        body = f"cd {shlex.quote(cwd)}\nexec $SHELL -l\n"
    else:
        binary = providers.executable(provider)
        if not binary:
            raise RuntimeError(f"{provider} CLI is not installed")
        environment = {"PATH": providers.environment_for(binary)["PATH"]}
        if provider == "ollama":
            from . import onboarding, ollama
            if resume_id:
                raise RuntimeError("Ollama exact conversation resume is not supported")
            model = ollama.validate_model(onboarding.preferences()['ollama_model'])
            args = [sys.executable, os.path.join(install.REPO, "blades", "ollama.py"),
                    "--binary", binary, "--model", model]
            prompt_text = ""  # Native chat is not a coding agent; never inject project instructions.
        elif provider == "codex":
            notify = [sys.executable, os.path.join(install.REPO, "hooks", "codex-notify.py")]
            args = [binary]
            if resume_id:
                args += ["resume", resume_id]
            args += ["--no-alt-screen", "--sandbox", "workspace-write",
                     "--ask-for-approval", "on-request", "-c", "notify=" + json.dumps(notify)]
        elif provider == "gemini":
            args = [binary, "--approval-mode", "default"]
            if resume_id:
                args += ["--resume", resume_id]
            if prompt_text:
                args += ["--prompt-interactive"]
            environment.update(install.gemini_launch_environment(name))
        else:
            args = [binary, "--settings", install.hooks_settings_path()]
            if resume_id:
                args += ["--resume", resume_id]
        command = shlex.join(args)
        if prompt_text:
            command += f' "$(cat {shlex.quote(prompt_file)})"'
        body = (f"cd {shlex.quote(cwd)}\n"
                f"export BLADE_ID={shlex.quote(name)} BLADES_PORT={config.PORT}\n"
                + "".join(f"export {key}={shlex.quote(value)}\n" for key, value in environment.items())
                + f"{command}\n")
    with open(script, "w", encoding="utf-8") as f:
        f.write("# sourced by an interactive login zsh\n" + body)
    os.chmod(script, 0o700)
    # Gate launch until scrollback/retention options are set, including fast exits.
    gate = config.path("run", name + ".ready")
    try:
        os.unlink(gate)
    except FileNotFoundError:
        pass
    launch = f"while [[ ! -e {shlex.quote(gate)} ]]; do sleep 0.05; done; source {shlex.quote(script)}"
    _run(["new-session", "-d", "-s", name, "-c", cwd,
          "-x", str(s["tmux_cols"]), "-y", str(s["tmux_rows"]),
          "-e", f"BLADE_ID={name}", "-e", f"BLADES_PORT={config.PORT}",
          "/bin/zsh", "-lic", launch], check=True)
    for key, value in (("status", "off"), ("history-limit", "100000"),
                       ("mouse", "on"), ("remain-on-exit", "on")):
        _run(["set-option", "-t", name, key, value], check=True)
    with open(gate, "w"):
        pass
    return name


def send_text(name, text, enter=True):
    """Type text into the blade. Literal, then a separate Enter so the
    Claude Code composer submits rather than treating it as a soft break."""
    if not exists(name):
        raise RuntimeError(f"no such blade {name}")
    _run(["send-keys", "-t", name, "-l", "--", text], check=True)
    if enter:
        time.sleep(0.35)
        _run(["send-keys", "-t", name, "Enter"], check=True)


def send_key(name, key):
    _run(["send-keys", "-t", name, key], check=True)


def capture(name, lines=80):
    r = _run(["capture-pane", "-p", "-t", name, "-S", f"-{lines}"])
    return r.stdout if r.returncode == 0 else ""


def kill(name):
    _run(["kill-session", "-t", f"={name}"])
    for ext in (".sh", ".prompt", ".ready"):
        try:
            os.remove(config.path("run", name + ext))
        except OSError:
            pass


def resize_window(name, cols, rows):
    _run(["resize-window", "-t", name, "-x", str(cols), "-y", str(rows)])


def capture_all(name):
    """Capture all retained scrollback and visible pane; raise on capture failure."""
    return _run(["capture-pane", "-p", "-J", "-t", f"{name}:", "-S", "-"], check=True).stdout


def pane_dead(name):
    r = _run(["display-message", "-p", "-t", f"{name}:", "#{pane_dead}"])
    return r.returncode == 0 and r.stdout.strip() == "1"
