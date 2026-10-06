# Setup guide

This guide works from a fresh clone and is also intended for an assistant helping someone install CLI Commander. Use the person's chosen host, folders, port, and providers. Do not copy another user's home paths, tailnet names, project index, credentials, or session data.

## Host and client requirements

| Component | Requirement |
| --- | --- |
| Host operating system | macOS is supported first. Linux is experimental; Windows hosting is unsupported. |
| Python | 3.11 or later; CI covers 3.11 and 3.13. |
| Terminal runtime | `tmux` on PATH and `/bin/zsh`. |
| Provider | At least one of Claude Code, OpenAI Codex CLI, or Google Gemini CLI. |
| Remote access | Tailscale on the host and each remote client, with a policy allowing only intended devices/users. |
| Browser | A current desktop or mobile browser. |
| Development only | Node.js for JavaScript syntax checks. |

Terminal.app discovery and migration, plus launchd service installation, are macOS-specific. Browser access works from other operating systems. CLI Commander launches the provider locally; providers may still contact their own cloud services for authentication and model requests.

## 1. Install the application

Clone the repository into a folder owned by the user. Keep the checkout separate from private application state and provider account directories.

Check `python3 --version` first: it must be Python 3.11 or later. Use the corresponding interpreter name (for example, `python3.13`) below if needed:

```sh
git clone https://github.com/WowWashington/CLI-Commander.git
cd CLI-Commander
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
```

Install tmux and any missing provider CLIs with the person's approved package manager or the providers' official installation instructions. Do not install all providers merely because CLI Commander supports them. `requirements.lock` pins application dependencies; it does not install tmux, Tailscale, or any provider CLI.

## 2. Choose a port and project folders

The default port is **5090**. Configure a different unprivileged port if it is already in use:

```sh
python -m blades configure --port 5091
```

The accepted range is 1024–65535. The selection is stored in private `server.json`. `BLADES_PORT` overrides the saved port when a process starts; remove that environment override if you want the saved setting to apply. A port change requires restarting the server. When there are live managed tmux sessions, changing the port is refused to protect their existing hook callback address. Finish and close those sessions first.

Register an existing project folder directly:

```sh
python -m blades add-project "$HOME/Projects/example" --name Example
```

Or use **Settings → Projects → Add project**, enter the folder path, and optionally provide a display name. **Remove** in the same list (or `python -m blades remove-project SLUG`) unregisters a project without touching its folder; close its live sessions first. Projects from a legacy `projects.md` index are hidden rather than removed. The private managed registry is `~/.cli_blades/projects.json`; project code stays in its existing folder. A particular Markdown table or `~/projects.md` file is not needed.

Code locations are parent folders that hold projects. Setup asks for them in its **Projects** step and suggests common folders that exist (such as `~/Projects`, `~/Code` or `~/Developer`). Manage them later in **Settings → Code locations**, or from the command line (repeat the flag for several folders):

```sh
python -m blades configure --projects-root "$HOME/Projects" --projects-root "$HOME/Code"
```

Locations are saved as `project_roots` in private `server.json`; an older single `projects_root` value is still honored. Without a saved choice, `~/Projects` is used when it exists. Their subfolders appear under **Unregistered folders**, ready to register. It does not automatically launch sessions or authorize editing every child folder. A legacy `~/projects.md` index remains an optional compatibility source; do not create or overwrite that file as part of a fresh installation.

## 3. Select providers and sign in

The first visit opens **Choose your assistants**. Choose the providers the user wants. **Complete sign-in** supplies provider-specific instructions and **Check sign-in again** refreshes discovery. **Where is your code?** then selects code locations and lets the user tick discovered folders to add as projects, or skip. Pending authentication does not prevent saving the setup choices.

The command-line equivalents are:

```sh
python -m blades setup --status
python -m blades setup --enable claude codex gemini
```

Pass only the desired provider IDs to `--enable`. Status discovery reports readiness without displaying credentials. A configured CLI path or an installed executable is not proof that its account is authenticated.

| Provider | Sign-in on the host | Account boundary |
| --- | --- | --- |
| Claude Code | Run `claude` and follow its native authentication prompts. | Uses Claude Code's own account and credential storage. |
| Codex CLI | Run `codex login` and follow its native flow. | Can sign in with ChatGPT; existing ChatGPT website chats are not imported. |
| Gemini CLI | Run `gemini` and choose its native authentication method. | Uses Gemini CLI's own account/configuration. |

Authentication availability, subscriptions, and organization policies are controlled by each provider. Follow the provider's official documentation when its prompts differ. Do not ask the user to paste passwords, API keys, cookies, or account files into CLI Commander, an issue, or a chat transcript. A setup agent should hand interactive sign-in to the user.

Normal CLI Commander sessions use per-launch monitoring: Claude settings, Codex notifications, and a Gemini settings overlay. These retain native approval behavior. Gemini administrative policies and system defaults are preserved. Global `install-hooks` is an optional legacy feature; it is not a prerequisite for managed sessions.

## 4. Start locally and connect remotely

```sh
python -m blades serve
```

Open `http://127.0.0.1:5090`, substituting your configured port. Add a project if needed and create a session with its **+** button.

For a remote client:

1. Sign the host and client into the intended tailnet.
2. Verify the Tailscale access policy permits only intended users/devices to reach the chosen host and port.
3. Open `http://<host-tailnet-name>:<port>` using that host's actual MagicDNS name or Tailscale IP.
4. Confirm you can reconnect to the same session after closing the browser tab.

No router port forwarding, public IP binding, Funnel, portal, or port-80 integration is required. Do not substitute the host's ordinary Wi-Fi/Ethernet address. The application intentionally rejects LAN and wildcard binds. If Tailscale cannot report its local address, the app remains loopback-only and periodically checks again.

The service grants terminal control to allowed clients. It is not a multi-tenant application with separate accounts or per-project access roles. HTTP runs inside Tailscale's encrypted transport; browser features that require an HTTPS secure context may still be unavailable. Reverse proxies require explicit origin design and are not part of this setup.

## 5. Optional macOS service

After local startup and provider selection work, stop the foreground server and install the login service:

```sh
python -m blades install-launchd
```

This uses `io.cli-blades.server` and `io.cli-blades.nightly` under the current user's LaunchAgents. The generated files contain the actual checkout and virtual-environment paths and belong outside Git. The service reads private server configuration, including the saved port. Re-run installation after moving the checkout or replacing its virtual environment.

Nightly maintenance applies the same inactivity policy as the running server. It does not close an active session merely because a scheduled time arrived. Reports are saved beneath CLI Commander's private data directory.

For upgrades, preserve private state and provider directories. Update the checkout/dependencies, run the tests, and restart only the CLI Commander server. Existing tmux processes survive a server restart, though provider settings newly added to the launcher apply only to newly launched sessions. Do not terminate managed sessions as an incidental part of upgrading or reinstalling.

## Reset setup without deleting work

Use **Settings → Assistants & setup → Manage assistants** to change provider choices, or **Recheck sign-in** to refresh readiness. **Reset setup** asks for a second tap, **Reset choices?**.

The command-line equivalent is:

```sh
python -m blades setup --reset
```

This reopens onboarding; it does not sign providers out, erase their credentials, stop running sessions, remove project registrations, or delete archives and handoffs. Do not delete `~/.cli_blades/` or provider account directories as a setup reset.

## Private state and environment overrides

The default data directory is `~/.cli_blades/`, separate from the repository. Back it up with appropriate access controls. It contains conversation transcripts and project paths, so do not attach it to public bug reports.

| Variable | Purpose |
| --- | --- |
| `BLADES_HOME` | Private application data directory. Keep outside the checkout. |
| `BLADES_PORT` | Process-level port override; takes precedence over saved configuration. |
| `BLADES_HOST` | Restrict binding to loopback or the current host's verified Tailscale address. Other bindings are rejected. |
| `BLADES_PROJECTS_ROOT` | Single code location; overrides and locks the saved locations. |
| `BLADES_PROJECTS_MD` | Optional legacy Markdown project index. |
| `BLADES_CLAUDE_BIN` | Explicit Claude executable path. |
| `BLADES_CODEX_BIN` | Explicit Codex executable path. |
| `BLADES_GEMINI_BIN` | Explicit Gemini executable path. |
| `BLADES_TMUX_SOCKET` | Dedicated tmux socket, primarily for isolated testing. |

Provider-specific variables, such as their account/configuration homes, belong to the provider. Do not move or replace them to work around a CLI Commander readiness indicator. Resolve the native sign-in first.

## Recovery checks and troubleshooting

- **No projects:** add an existing folder through Settings or `add-project`. A fresh install starts without someone else's project index or quick actions.
- **Provider unavailable:** check its executable and selection. Run `setup --status`, then its native sign-in command if necessary.
- **Remote connection refused:** check Tailscale connectivity, access policy, the configured port, and whether the server is running. Do not relax the bind boundary to `0.0.0.0`.
- **Host or Origin rejected:** use the same approved host and port for the page and WebSocket connection. Forwarded headers are intentionally not trusted.
- **Session remains open after inactivity:** inspect its status. Busy/unknown/permission-waiting/background activity, protection, or an unreadable transcript prevents automatic closure.
- **Gemini is at its initial sign-in screen:** no conversation file may exist yet. Explicit archive-and-close can save terminal output; it cannot create a resumable conversation that never started.
- **Gemini `/clear`:** use a new Blade to track the new conversation. The old Blade's identity is intentionally not silently reassigned.
- **Archive fails:** preserve the live session and resolve disk space or file access before retrying. Do not bypass the archive check by killing tmux.
- **Changing ports:** close managed sessions intentionally, remove conflicting `BLADES_PORT` overrides, save the port, then restart the server.

Automatic cleanup defaults to 24 hours plus a 120-second warning. It requires confirmed completion and a saved transcript, and does not commit or push. Browser drafts remain local to that browser. Recovery archives do not replace backups of source files or preserve live subprocess memory.

## Instructions for a setup agent

1. Read this guide and inspect the chosen host for prerequisites. Do not assume another machine's username, home path, tailnet name, service labels, port, or installed providers.
2. Keep configuration and audit output outside the repository. Confirm the selected project paths exist; register only intended folders.
3. Configure the desired port and provider choices. Preserve existing sessions, provider credentials, Tailscale policy, and unrelated application settings.
4. Let the user complete native provider authentication. Never capture or report secret values.
5. Verify local reachability and the restricted bind addresses, then verify remote reachability from an authorized tailnet device where available.
6. Exercise reconnect and recovery using a throwaway project/session. Use the opt-in fake-provider integration test when real model use is unnecessary.
7. Record a concise handoff: selected port, state-directory location, enabled providers, service status, checks completed, and anything awaiting the user's sign-in. Keep machine-specific handoffs private.

Before publishing a fork, scan both the current files and reachable history for secrets and personal details. Ignoring a file or removing it from the latest commit does not remove earlier copies. Review bundled dependency notices and choose a license for your own contributions explicitly.
