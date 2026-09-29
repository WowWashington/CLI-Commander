"""CLI: python -m blades <command>

  serve                 run the web server (default)
  configure --port N    save a private listening port (restart required)
  add-project PATH      register a project folder (--name NAME optional)
  setup --status        discover installed assistants and sign-in readiness
  setup --enable IDS    enable chosen assistants (e.g. claude codex gemini)
  setup --reset         reset assistant choices; keep sessions and credentials
  install-hooks         register hook relay in ~/.claude/settings.json
  install-launchd       install + load service and nightly plists
  ls                    list live blades
  open <slug> [provider] open Claude (default), Codex or Gemini for a project
  resume <blade>        resume a saved exact conversation
  send <blade> <text>   type text into a blade
  run <command-id> [blade|all]
  nightly               apply inactivity policy, then write the audit
  audit                 write a private project audit under ~/.cli_blades/reports
"""
import json
import sys
import urllib.request

from . import config


def _api(method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{config.PORT}{path}",
                                 method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-Blades-Request": "1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main(argv):
    cmd = argv[0] if argv else "serve"
    if cmd == "configure":
        import argparse
        from . import server_config
        parser = argparse.ArgumentParser(description="Configure private CLI Commander host settings; restart afterwards")
        parser.add_argument("--port", type=int)
        parser.add_argument("--projects-root")
        opts = parser.parse_args(argv[1:])
        try:
            result = server_config.configure({key: value for key, value in vars(opts).items() if value is not None})
            print(json.dumps(result, indent=2))
        except (ValueError, RuntimeError) as exc:
            parser.error(str(exc))
    elif cmd == "add-project":
        import argparse
        from . import projects
        parser = argparse.ArgumentParser(description="Register an existing folder in your private CLI Commander project list")
        parser.add_argument("path")
        parser.add_argument("--name")
        opts = parser.parse_args(argv[1:])
        try:
            print(json.dumps(projects.add_project(opts.path, opts.name), indent=2))
        except ValueError as exc:
            parser.error(str(exc))
    elif cmd == "setup":
        import argparse
        from . import onboarding
        parser = argparse.ArgumentParser(description="Discover assistants; provider credentials remain with each CLI")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--status", action="store_true")
        group.add_argument("--enable", nargs="+", choices=("claude", "codex", "gemini"))
        group.add_argument("--reset", action="store_true")
        opts = parser.parse_args(argv[1:])
        try:
            result = onboarding.reset() if opts.reset else onboarding.configure({"enabled_providers": opts.enable}) if opts.enable else onboarding.status(refresh=True)
            print(json.dumps(result, indent=2))
        except ValueError as exc:
            parser.error(str(exc))
    elif cmd == "serve":
        import logging, threading, time
        from werkzeug.serving import make_server
        from .app import create_app
        a = create_app()
        log = logging.getLogger("blades.serve")
        bound = set()

        def bind(addr):
            srv = make_server(addr, config.PORT, a, threaded=True)
            bound.add(addr)
            log.info("listening on http://%s:%d", addr, config.PORT)
            threading.Thread(target=srv.serve_forever, daemon=True, name=f"bind-{addr}").start()

        for addr in config.bind_addresses():
            bind(addr)
        if not config.HOST:
            def watch():
                while True:
                    time.sleep(30)
                    ts = config.tailscale_ip()
                    if ts and ts not in bound:
                        try:
                            bind(ts)
                        except (Exception, SystemExit) as e:
                            log.warning("bind %s failed: %s", ts, e)
            threading.Thread(target=watch, daemon=True).start()
        from . import lifecycle
        from .app import _reconcile
        def maintain():
            while True:
                time.sleep(15)
                try:
                    _reconcile()
                    lifecycle.sweep()
                except Exception:
                    log.exception("Session maintenance paused after an error; sessions preserved")
        threading.Thread(target=maintain, daemon=True, name="session-maintenance").start()
        while True:
            time.sleep(3600)
    elif cmd == "install-hooks":
        from . import install
        print("hooks added:", install.install_hooks() or "none (already present)")
    elif cmd == "uninstall-hooks":
        from . import install
        install.uninstall_hooks(); print("hooks removed")
    elif cmd == "install-launchd":
        from . import install
        for label, rc, err in install.install_launchd():
            print(label, "loaded" if rc == 0 else f"rc={rc} {err}")
    elif cmd == "ls":
        for b in _api("GET", "/api/blades")["live"]:
            print(f"{b['name']:40} {b['state']:10} {b['note'] or ''}")
    elif cmd == "open":
        print(_api("POST", "/api/blades", {"slug": argv[1], "provider": argv[2] if len(argv) > 2 else "claude"})["name"])
    elif cmd == "resume":
        print(_api("POST", f"/api/blades/{argv[1]}/resume", {})["name"])
    elif cmd == "send":
        print(_api("POST", f"/api/blades/{argv[1]}/send", {"text": " ".join(argv[2:])}))
    elif cmd == "run":
        target = argv[2] if len(argv) > 2 else "all"
        print(_api("POST", f"/api/commands/{argv[1]}/run",
                   {"blades": "all" if target == "all" else [target]}))
    elif cmd == "audit":
        r = _api("POST", "/api/audit/run")
        print(r["report_path"]); print(r["counts"])
    elif cmd == "nightly":
        import time
        print("inactivity cleanup:", _api("POST", "/api/maintenance/run", {}))
        deadline = time.time() + 25 * 60
        while time.time() < deadline:
            live = _api("GET", "/api/blades")["live"]
            if not any(b["state"] == "closing" for b in live):
                break
            time.sleep(15)
        r = _api("POST", "/api/audit/run")
        print("audit:", r["report_path"], r["counts"])
    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
