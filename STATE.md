# CLI Commander — Project State

Read this file for implementation context. Host-specific settings and operational notes belong in the private application data directory or ignored `STATE.local.md`.

## Purpose

CLI Commander is a compact private web console for Claude Code, OpenAI Codex CLI and Google Gemini CLI. It keeps tmux sessions available across browser disconnects and device changes. Access is restricted to localhost and Tailscale; ordinary LAN and public listeners are rejected.

Public repository: https://github.com/WowWashington/CLI-Commander. The Python package (`blades`), `BLADES_*` environment variables, private state directory and service identifiers retain their existing names for compatibility.

The supported host is macOS with Python 3.11+, zsh and tmux. Clients can be phones, tablets or desktop browsers. Linux is experimental; Windows hosting is unsupported.

## User experience

- First-run onboarding discovers known CLI installation locations, lets users enable providers, checks supported sign-in status commands, and guides native sign-in. Credentials stay with each provider.
- A single + per project opens a compact chooser of enabled assistants.
- Onboarding steps: Assistants → Sign-in → Projects ("Where is your code?": choose code locations from detected common folders or a typed path, tick discovered subfolders to register, or skip) → Ready.
- Settings can manage assistants, recheck sign-in, reset only onboarding choices, add/remove code locations, add/remove registered projects (folder untouched; blocked while live sessions exist; legacy projects.md rows can only be hidden), and stage a private port change.
- The default port is 5090, configurable through `python -m blades configure --port N` or Settings. Restart is required; live sessions block port changes to preserve their hook connections.
- The default inactivity window is 24 hours plus a 120-second grace period. Busy, protected, shell and unconfirmed sessions remain open.
- Mobile composition keeps drafts in that browser; reconnect never replays ambiguous input.

## Architecture

- `config.py`, `server_config.py`: private settings, atomic writes, safe binds and configurable host port.
- `onboarding.py`, `providers.py`: discovery, redacted authentication summaries, enablement, installation choices and exact native transcript lookup.
- `projects.py`, `audit.py`: private JSON project registry (add/remove, duplicate check by folder identity for case-insensitive paths), optional legacy Markdown registry, project facts and private audit reports.
- `locations.py`: code locations (`project_roots` in `server.json`; legacy single `projects_root` honored; default `~/Projects`; `BLADES_PROJECTS_ROOT` overrides and locks), suggestions, preview and unregistered-folder discovery.
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

**Last updated**: 2026-10-06
**State**: Onboarding, portability, project management and touch-device improvements implemented. 145 tests pass, including real tmux/WebSocket integration.
**Projects**: Code locations (`project_roots` in server.json; legacy `projects_root` honored; default `~/Projects`; `BLADES_PROJECTS_ROOT` overrides). Setup has a Projects step ("Where is your code?"). Settings manages code locations and registered projects (remove leaves folders untouched; blocked while sessions are live; legacy projects.md rows can only be hidden). The Add project dialog creates a new folder (default: first code location) with a starter STATE.md and optional `git init`, registers it and opens the assistant chooser, or registers an existing folder. Folder identity (dev/inode) prevents case-variant duplicates on macOS.
**Sessions**: The header strip and a sidebar "Open sessions" list show every live session, most urgent first. New Claude sessions launch with `--name "<Project> · <Mon D>[ #n]"`. An unmodified pre-rename "CLI Blades" prompt template is replaced by the current default. Claude's internal helpers send SubagentStop without SubagentStart; untracked SubagentStops no longer flip idle/attention sessions back to busy.
**Terminal**: Android keyboards compose each word; in Live typing mode IME events are intercepted on the terminal wrapper's capture phase and composition changes are sent live as minimal backspace+tail diffs, and the hidden textarea is emptied after each input. Settings → "Typing on this device" (per browser: auto/live/standard; auto = Live on touch devices). Verified on a Samsung tablet (Samsung Keyboard, Edge); iPhone/iPad, Gboard and SwiftKey untested. Hidden tabs keep their terminal connection for 2 minutes; connections attach at the browser's size (`/ws/term/<name>?cols=&rows=`), the size is re-sent on activate/connect/visible/focus, sizes are clamped to 20–1000 columns and 5–500 rows, and reconnect keeps scrollback. Optional keyboard diagnostics: create `<BLADES_HOME>/kbdebug.on` to log touch-keyboard events to `keyboard-debug.log` (contains keystrokes; delete both afterwards).
**Instances**: `instance_name` (Settings → Connection → Name, `configure --name`, `BLADES_INSTANCE_NAME`) labels an installation in the header and tab title.
**Publication**: This repository starts with clean source history. Private application state, credentials and host-specific deployment files are excluded; host-specific notes live in the ignored `STATE.local.md`. A license for the project's own code is not yet selected; bundled assets have separate license notices.
**Next steps**: Turn the jsdom keyboard replay into a permanent test with per-device fixtures; test iPhone/iPad and Gboard. Make the message box the primary touch input (send `/` commands without Enter), stop terminal taps raising the keyboard, and fix copying from the terminal on tablets. Add Ollama (plain `ollama run` chat, and agents via `ollama launch` after checking what it writes to agent settings). Exercise first-time native provider sign-in on each new host.
