# CLI Commander — Project State

Read this file for implementation context. Host-specific settings and operational notes belong in the private application data directory or ignored `STATE.local.md`.

## Purpose

CLI Commander is a compact private web console for Claude Code, OpenAI Codex CLI and Google Gemini CLI. It keeps tmux sessions available across browser disconnects and device changes. Access is restricted to localhost and Tailscale; ordinary LAN and public listeners are rejected.

Public repository: https://github.com/WowWashington/CLI-Commander. The Python package (`blades`), `BLADES_*` environment variables, private state directory and service identifiers retain their existing names for compatibility.

The supported host is macOS with Python 3.11+, zsh and tmux. Clients can be phones, tablets or desktop browsers. Linux is experimental; Windows hosting is unsupported.

## User experience

- First-run onboarding discovers known CLI installation locations, lets users enable providers, checks supported sign-in status commands, and guides native sign-in. Credentials stay with each provider.
- A single + per project opens a compact chooser of enabled assistants.
- Settings can manage assistants, recheck sign-in, reset only onboarding choices, add project folders, and stage a private port change.
- The default port is 5090, configurable through `python -m blades configure --port N` or Settings. Restart is required; live sessions block port changes to preserve their hook connections.
- The default inactivity window is 24 hours plus a 120-second grace period. Busy, protected, shell and unconfirmed sessions remain open.
- Mobile composition keeps drafts in that browser; reconnect never replays ambiguous input.

## Architecture

- `config.py`, `server_config.py`: private settings, atomic writes, safe binds and configurable host port.
- `onboarding.py`, `providers.py`: discovery, redacted authentication summaries, enablement, installation choices and exact native transcript lookup.
- `projects.py`, `audit.py`: private JSON project registry, optional legacy Markdown registry, project facts and private audit reports.
- `tmuxctl.py`, `store.py`: provider launch, persistent identities and lifecycle evidence, retained ended panes.
- `lifecycle.py`, `winddown.py`: serialized interaction, inactivity scheduling, immutable archives and missing-only restoration. Uncertainty or archive failure preserves the process.
- `app.py`, `security.py`: same-origin HTTP/WebSocket API, terminal connections, project/setup/configuration routes and maintenance.
- `install.py`, `hooks/`: per-launch monitoring, generic macOS launchd services and safe migration of this checkout's older service.
- `legacy.py`: conservative optional migration of existing Terminal.app Claude sessions.
- `static/`: compact responsive dashboard, setup wizard, provider chooser, terminal, drafts and recovery controls.

Private state defaults to `~/.cli_blades/`, outside the checkout. Provider account files, project lists, port configuration, archives, databases, generated scripts, local infrastructure and secret-scan reports must never be committed. See `.gitignore` and `docs/SETUP.md`.

## Recovery guarantees and limits

Automatic close requires a provider completion event and readable transcript. Blades archives session identity, retained terminal output, native conversation history and a project handoff before requesting exit. Claude adds matching recovery directories when present. Existing files are never replaced by an older archive during resume.

This preserves recorded conversations, not running process memory or a backup of project files. Archives have no automatic retention limit. Provider storage formats can change. Gemini authentication screens have no conversation transcript yet and support explicit terminal-only archival; missing previously recorded history still blocks closure. Resetting setup never deletes sessions, archives, project registrations, provider credentials or port settings.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/python -m blades serve
.venv/bin/python -m unittest discover -s tests -v
BLADES_INTEGRATION=1 .venv/bin/python -m unittest discover -s tests -v
node --check blades/static/app.js
```

See README.md and docs/SETUP.md for installation, native authentication and setup-agent instructions. Tests use isolated state; real terminal integration uses a unique tmux socket and fake provider without model credentials.

## Current status

**Last updated**: 2026-09-28
**State**: Shareable onboarding and portability upgrade implemented and deployed. All 130 tests pass in this release with independently installed dependencies and an isolated user profile, including real tmux and WebSocket integration. The earlier portability build also passed from a checkout path containing spaces. Mobile onboarding, selected-provider launching, first project registration, persistence, and reset were verified. Existing managed sessions survive service migration/restart.
**Recent fix**: Setup command Copy buttons now use a synchronous selection-based clipboard fallback on HTTP Tailscale origins, restore focus/selection, and report failure explicitly when copying is blocked. Verified that the Gemini command reaches the macOS system clipboard; JavaScript syntax and diff checks pass.
**Publication**: This repository starts with clean source history. Private application state, credentials and host-specific deployment files are excluded. A license for the project's own code is not yet selected; bundled assets have separate license notices.
**Next steps**: Exercise first-time native provider sign-in on each new host; background status checks deliberately do not authenticate providers or test a model request.
