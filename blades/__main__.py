"""CLI: python -m blades <command>

  serve                 run the web server (default)
  configure --port N    save a private listening port (restart required)
  configure --projects-root DIR  set code locations (repeat for several)
  configure --name LABEL         label this installation (header and tab title)
  add-project PATH      register a project folder (--name NAME optional)
  remove-project SLUG   unregister a project; its folder is left untouched
  voice-key             store an Anthropic API key (Keychain) for the XR Talk assistant
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
        parser.add_argument("--name", help='label for this installation, e.g. "CLI Dev"; empty clears it')
        parser.add_argument("--assistant-keychain-service", metavar="SERVICE",
                            help="use an existing Keychain item holding an Anthropic API key for the Talk assistant")
        parser.add_argument("--projects-root", action="append", metavar="FOLDER",
                            help="code location whose subfolders are offered as projects; repeat for several")
        opts = parser.parse_args(argv[1:])
        payload = {"port": opts.port} if opts.port is not None else {}
        if opts.name is not None:
            payload["instance_name"] = opts.name
        if opts.assistant_keychain_service is not None:
            payload["assistant_keychain_service"] = opts.assistant_keychain_service
        if opts.projects_root:
            payload["project_roots"] = opts.projects_root
        try:
            result = server_config.configure(payload)
            if opts.projects_root:
                from . import locations
                result["code_locations"] = [item["path"] for item in locations.status()["locations"]]
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
    elif cmd == "remove-project":
        import argparse
        from . import projects, store
        parser = argparse.ArgumentParser(description="Unregister a project; its folder and files are not changed")
        parser.add_argument("slug")
        opts = parser.parse_args(argv[1:])
        if any(b["slug"] == opts.slug for b in store.live()):
            parser.error("Close this project's live sessions before removing it")
        try:
            projects.remove_project(opts.slug)
            print(json.dumps({"removed": opts.slug}))
        except (ValueError, LookupError) as exc:
            parser.error(str(exc).strip("'"))
    elif cmd == "voice-key":
        # Stores the key in the login Keychain; `security` prompts for it, so it
        # never appears in shell history, process arguments or files.
        import subprocess
        from . import agent
        print("Paste your Anthropic API key when asked (twice). It is stored in your macOS Keychain.")
        r = subprocess.run(["security", "add-generic-password", "-U", "-s", agent.KEYCHAIN_SERVICE,
                            "-a", agent.KEYCHAIN_ACCOUNT, "-l", "CLI Commander assistant", "-w"])
        print("Saved. The XR Talk assistant is ready." if r.returncode == 0 else "Not saved.")
        sys.exit(r.returncode)
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
