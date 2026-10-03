# CLI Commander

A compact, private browser console for Claude Code, OpenAI Codex CLI, and Google Gemini CLI. Run the CLIs on your computer, then use the same sessions from a phone, tablet, or another computer over Tailscale.

Sessions run in tmux, so closing a browser or switching devices does not end the work. CLI Commander shows which sessions are running, idle, or waiting for you, and saves recovery history before closing confirmed inactive sessions.

## Get started

The supported host is **macOS**, with **Python 3.11 or later**, **tmux**, and **zsh**. Install at least one supported provider CLI separately. Tailscale is required for access from other devices; local use works without it. Linux is experimental; the launcher expects `/bin/zsh`, and Terminal.app integration and launchd installation are macOS features. Windows hosting is not supported. Client devices only need a modern browser.

Clone the repository, then use a `python3` interpreter that is version 3.11 or later:

```sh
git clone https://github.com/WowWashington/CLI-Commander.git
cd CLI-Commander
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m blades configure --port 5090
python -m blades serve
```

Open [the local console](http://127.0.0.1:5090). On first run, **Choose your assistants**, follow the sign-in instructions, and **Finish setup**. Add an existing folder from **Settings → Projects → Add project**. No pre-existing project index is required.

For scripted setup:

```sh
python -m blades setup --status
python -m blades setup --enable claude codex gemini
python -m blades add-project "$HOME/Projects/example" --name Example
```

Enable the assistants you intend to use. Each provider needs its own installation and account; enabling one does not install it or authenticate it. See the [complete setup guide](docs/SETUP.md), including instructions for a setup agent.

## Using the console

- Tap a project's **+**, then choose **Claude**, **Codex · ChatGPT**, or **Gemini**. **New session** also includes a project picker.
- Search and the **Live / Running / Needs you / Idle / Saved** filters keep the session list compact.
- Reconnect after a network interruption, browser sleep, or device switch. Multiple viewers attach to the same process.
- When a newer interface is available, **Update available · Reload** refreshes it explicitly. Your draft is saved first and running sessions stay open.
- Use the mobile key bar for Esc, Tab, Ctrl+C, arrows, and Enter. Cmd/Ctrl+Enter sends the composer; Cmd/Ctrl+K searches projects.
- The composer retains unsent drafts in that browser's local storage. Drafts do not sync between devices, and uncertain submissions are never automatically resent.
- **Resume** opens the exact saved provider conversation. **Fresh** starts a new conversation with the project handoff. **View** shows saved output and the handoff.
- Protect a session with its lock to exclude it from inactivity cleanup and batch commands.

Codex CLI can use a ChatGPT account. Ordinary ChatGPT website conversations are separate and are not imported into CLI Commander. All providers retain their native authentication and account storage. Sign-in belongs in the provider's own interface, not a CLI Commander form.

## Inactivity and recovery

The default is **24 hours of confirmed inactivity**, followed by a **120-second grace period**. Settings can change these values; a timeout of `0` disables automatic closure. **Keep open** extends the timer. Leaving a browser tab open does not hold a session open indefinitely.

Automatic closure requires provider completion evidence and a readable conversation transcript. It does not send a model prompt, commit, push, reset, or stash. Before asking the CLI to exit, CLI Commander saves:

1. The provider, project, session identity, and exact conversation ID.
2. All retained tmux scrollback; older lines may already have been evicted.
3. The native conversation transcript. Claude recovery bundles also include matching subagent, tool-result, and file-history files when available.
4. A project handoff and recovery metadata.

Archives are separate snapshots with no automatic deletion policy. Resume can restore missing provider history without overwriting newer files. Provider storage formats and native resume rules still apply.

Busy, protected, shell, permission-waiting, and unconfirmed sessions stay open. Archive errors and exit timeouts preserve the session. Explicit background tool activity conservatively blocks automatic cleanup until a fresh provider lifecycle; arbitrary detached processes are not inferred from screen silence. Gemini `/clear` leaves status unconfirmed: open a fresh Blade to track the new conversation.

Manual **Wind down** asks an idle assistant to checkpoint first. Stock commands preserve work without committing or pushing. **Archive & force close** is a separate, confirmed action that snapshots before stopping the process. Saved conversation history is not a backup of project files or running process memory. Back up your projects and CLI Commander data separately.

## Private access

CLI Commander listens only on loopback and this host's Tailscale address. It rejects wildcard, public, and ordinary LAN bind addresses. If the local Tailscale client cannot identify its address, CLI Commander stays local and checks again later.

Connect from another tailnet device using `http://<host-tailnet-name>:5090`, replacing the hostname and port with your own. No portal, reverse proxy, public hosting, or port-80 service is required.

**Your Tailscale access policy is the authentication boundary.** A device allowed to reach this service can control its terminals. Restrict access to the intended users and devices. CLI Commander does not modify tailnet policy or enable public Funnel access.

The app checks peer addresses, Host and Origin headers; browser writes require a same-origin request marker, and WebSockets require a matching Origin. Hook callbacks are local-only. Forwarded headers are not trusted. Direct HTTP travels inside the Tailscale tunnel, but some browser features require HTTPS. A reverse proxy needs a separately reviewed origin configuration; arbitrary forwarding headers are not a supported shortcut.

Claude keeps its normal permission behavior. Codex uses workspace-write sandboxing with on-request approvals. Gemini keeps its default approval mode and existing administrative policies. Provider monitoring is scoped to each launch; normal setup does not require editing global provider hooks.

## Configuration and maintenance

For compatibility with existing installations, CLI Commander retains the `blades` Python module, `BLADES_*` environment variables, `~/.cli_blades/` state directory, and `io.cli-blades.*` service labels.

Private data lives in `~/.cli_blades/`, outside the checkout. This includes the project registry, settings, session database, generated launch files, handoffs, reports, and history archives.

```sh
# Save a different port; finish and close managed sessions first.
python -m blades configure --port 5091

# Reopen onboarding without deleting sessions, archives, projects or accounts.
python -m blades setup --reset

# Optional macOS login service and nightly maintenance.
python -m blades install-launchd
```

A port change requires a server restart. The saved default is overridden by `BLADES_PORT` in the environment of the process being started. Managed sessions must be closed before changing the port so their hook callbacks do not become disconnected. Full environment-variable and migration details are in [SETUP.md](docs/SETUP.md).

## Development checks

```sh
python -m unittest discover -s tests -v
BLADES_INTEGRATION=1 python -m unittest discover -s tests -v
node --check blades/static/app.js
```

Unit tests use temporary state and mocked providers. The opt-in integration test uses real tmux and WebSockets with a fake provider on a unique socket; it does not require model credentials. CI runs the suite on macOS with Python 3.11 and 3.13. `requirements.lock` pins the dependency set; `requirements.txt` lists direct minimum requirements.

For a secret scan with Gitleaks:

```sh
gitleaks git --log-opts="--all --full-history" --redact=100 .
gitleaks dir --redact=100 .
```

Secret scanners do not establish that commit history is free of personal information. Review history, author metadata, screenshots, examples, and local files before publishing a fork.

## Provider documentation and notices

- Claude Code: [installation](https://code.claude.com/docs/en/setup), [resume](https://code.claude.com/docs/en/common-workflows#resume-previous-conversations), and [hooks](https://code.claude.com/docs/en/hooks).
- OpenAI Codex: [CLI](https://developers.openai.com/codex/cli/) and [authentication](https://developers.openai.com/codex/auth/).
- Gemini CLI: [installation](https://geminicli.com/docs/get-started/installation/), [authentication](https://geminicli.com/docs/get-started/authentication/), and [session management](https://geminicli.com/docs/cli/session-management/).
- Tailscale: [access controls](https://tailscale.com/kb/1018/acls).

Bundled terminal assets have their own licenses, listed in [third-party notices](blades/static/vendor/THIRD_PARTY_NOTICES.md). A license for CLI Commander's own code has not yet been selected; dependency licenses do not license this project's code.

### Optional Ollama chat

[Ollama setup and limits](docs/OLLAMA.md): explicitly choose an installed local model. Chat only, with a cloud-disabled loopback backend and retained terminal output; no automatic close or exact conversation resume. Source checkpoint only; deployment is separate.
