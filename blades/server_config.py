"""Private host configuration. Port changes never strand a running CLI's hooks."""
import os
from pathlib import Path
import socket
import threading

from . import config, tmuxctl

_lock = threading.RLock()


def status():
    saved = config.server_settings()
    port = saved.get("port", config.PORT)
    override = os.environ.get("BLADES_PORT")
    return {"port": config.PORT, "configured_port": port,
            "env_override": bool(override), "restart_required": port != config.PORT,
            "next_url": f"http://127.0.0.1:{port}",
            "instructions": "Close live CLI Commander sessions before changing ports so their status hooks stay connected. "
                "Ask your setup agent to restart CLI Commander after saving. For a launchd installation, run "
                "`.venv/bin/python -m blades install-launchd`; for a foreground server, stop and start it again. "
                + ("BLADES_PORT currently overrides the saved port; remove that override when restarting. " if override else "")
                + "On another device, keep the host's Tailscale name and use the new port. Access stays local/Tailscale only."}


def configure(payload):
    if not isinstance(payload, dict) or not payload or set(payload) - {"port", "projects_root"}:
        raise ValueError("Choose a port or project discovery folder")
    with _lock:
        saved = config.server_settings()
        if "port" in payload:
            port = config.valid_port(payload["port"])
            if port != config.PORT:
                if tmuxctl.list_sessions():
                    raise RuntimeError("Close live CLI Commander sessions before changing ports; their status hooks use the current port")
                if os.environ.get("BLADES_PORT") and int(os.environ["BLADES_PORT"]) != port:
                    raise RuntimeError("BLADES_PORT overrides the saved port. Ask your setup agent to remove that override before changing ports")
                for address in config.bind_addresses():
                    family = socket.AF_INET6 if ":" in address else socket.AF_INET
                    with socket.socket(family, socket.SOCK_STREAM) as probe:
                        try:
                            probe.bind((address, port))
                        except OSError as exc:
                            raise RuntimeError(f"Port {port} is unavailable on a private listener; choose another port") from exc
            saved["port"] = port
        if "projects_root" in payload:
            folder = payload["projects_root"]
            if not isinstance(folder, str) or not folder.strip():
                raise ValueError("Choose an existing project discovery folder")
            folder = Path(folder).expanduser()
            if not folder.is_absolute() or not folder.is_dir():
                raise ValueError("Project discovery folder must be an existing absolute path")
            saved["projects_root"] = str(folder.resolve())
        config.save_json(config.path("server.json"), saved)
        return status()
