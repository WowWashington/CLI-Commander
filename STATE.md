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

## Ollama source checkpoint (2026-10-03)

An isolated source-only change adds explicitly selected **Ollama chat** with a
saved installed model name. Launch requires the loopback daemon to confirm cloud
is disabled and to list that exact model. Missing backend/model/policy evidence
blocks launch; no model call, download, sign-in or service change occurs during
setup. Native chat gets no automatic project instructions or coding tools.

Ollama completion and native transcript identity are unavailable: no automatic
wind-down, bulk save-state or exact conversation resume is advertised. Existing
terminal snapshots and failure-preserving close behavior remain in use. See
[docs/OLLAMA.md](docs/OLLAMA.md) for setup, experimental status-API compatibility,
native CLI/model-race limits and bounded terminal retention.

Safe checks: `python3 -B tests/test_ollama_offline.py` (14 isolated cases), plus
`node tests/test_ollama_setup_offline.js` and `node --check blades/static/app.js`.
Setup distinguishes saved model readiness from unsaved edits and never labels an
unavailable backend as signed in or ready. Existing onboarding expectations include the
new opt-in entry; their application-importing suites were not run in this source
pass. No live model, tmux session, provider configuration, service or active
checkout was touched. Deployment and live acceptance remain separate. This is
based on the public main checkpoint; the separate unpublished project-location
and tablet-input changes need deliberate integration later.

## Current status

**Last updated**: 2026-10-03
**State**: Ollama provider source candidate awaiting review/deployment; earlier release status follows. Shareable onboarding and portability upgrade implemented and deployed. All 130 tests pass in this release with independently installed dependencies and an isolated user profile, including real tmux and WebSocket integration. The earlier portability build also passed from a checkout path containing spaces. Mobile onboarding, selected-provider launching, first project registration, persistence, and reset were verified. Existing managed sessions survive service migration/restart.
**Recent fix**: Setup Copy buttons include a synchronous HTTP fallback. Clipboard writes were verified from fresh loopback and Tailscale pages after replacing the native macOS clipboard with a unique test value. Existing tabs retain old code until reloaded; content-versioned assets and an explicit update/reload notice now make interface updates visible without automatically interrupting sessions.
**Publication**: This repository starts with clean source history. Private application state, credentials and host-specific deployment files are excluded. A license for the project's own code is not yet selected; bundled assets have separate license notices.
**Next steps**: Exercise first-time native provider sign-in on each new host; background status checks deliberately do not authenticate providers or test a model request.
