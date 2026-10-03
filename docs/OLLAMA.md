# Ollama chat provider — source checkpoint

This opt-in provider opens native `ollama run` chat in a retained Commander terminal.
It is not a coding agent: Commander does not give it repository tools, a project
startup prompt, save-state instructions or permission to edit files. No provider
account is changed. This source checkpoint has not been deployed or smoke-tested
with a live model.

## Setup

1. Install Ollama and prepare a **local** model separately. Start its daemon on
   `127.0.0.1:11434` with cloud disabled. Ollama documents `OLLAMA_NO_CLOUD=1`
   in the server environment or `disable_ollama_cloud: true` in its server
   configuration, followed by an Ollama restart. Commander does not change or
   restart that service.
2. In Commander’s assistant setup, explicitly select **Ollama chat** and enter
   the exact name shown by `ollama list`, including the tag. It is not selected
   by default even when installed. The private onboarding file saves the model
   and enablement choice. Existing Claude, Codex and Gemini choices are retained.
3. Finish setup to save the model and check it. Recheck examines the saved model;
   an unsaved edit is preserved but not used by that check. Setup reports pending
   readiness instead of claiming sign-in or successful backend access. Open a project’s Ollama chat. The project is just its
   working directory; no repository content or handoff is sent automatically.

Readiness first requires the local daemon’s `/api/status` response to report
`cloud.disabled` as boolean `true`. Missing, malformed or unsupported status
blocks launch, as does a timeout or redirect. This upstream endpoint is
experimental: older versions without it remain unavailable rather than having
local-only behavior guessed. Names containing `cloud` are also rejected.
The CLI is pinned to the loopback daemon, bypasses HTTP proxies, and receives
`OLLAMA_NO_CLOUD=1`; that child variable alone is **not** treated as proof of
an already-running daemon’s policy. Keep the server’s cloud-disabled policy in
place throughout the session.

Checks only read daemon policy and list models. They do not generate, sign in,
install, download or start Ollama. A model missing from the exact installed list
blocks launch. The wrapper rechecks immediately before starting native chat.
The native CLI still controls subsequent interaction: if the model is removed
concurrently after this check, its own missing-model behavior applies. This is
not a sandbox for native CLI commands or protection against a separately changed
Ollama installation/configuration.

## Interaction and retained history

- Type into the existing terminal or message composer. Reconnecting to the same
  running tmux pane keeps the native chat process; disconnected input is not
  queued or replayed. Native commands such as `/bye` remain the user’s choice.
- Commander does not receive reliable Ollama completion hooks or an exact native
  conversation ID/transcript. Its state stays unconfirmed; it is excluded from
  automatic wind-down and bulk save-state commands. Screen silence is not proof
  that generation finished. Safe automatic close and exact resume are unsupported.
- After a native exit, the retained pane can be archived through the existing
  reconciliation path. Explicit force-close first saves a terminal snapshot;
  capture/archive failure leaves the session running. These archives contain
  retained terminal output, **not** an independently resumable Ollama conversation.
  Older scrollback can be evicted. Process memory/context is not a backup.
- An unavailable backend, unlisted model or unconfirmed cloud policy is reported
  before creating a session. A later startup failure stays visible in the
  retained terminal. There is no fallback to another provider or model.

## Offline verification

```sh
python3 -B tests/test_ollama_offline.py
node tests/test_ollama_setup_offline.js
node --check blades/static/app.js
```

The isolated test module extracts actual functions and supplies fake provider,
HTTP, tmux and configuration dependencies. Only synthetic temporary files and
in-memory SQLite are used. It covers explicit choices, validation, cloud-policy
failure, backend errors, launch quoting, literal input, unknown completion,
history preservation and failed archival. Production application/config imports,
network connections and process execution are blocked. A separate actual-source
JavaScript fixture uses a fake DOM/API to check opt-in, model persistence, honest
readiness labels and failed-save behavior. Deployment, a disposable
live model check, visual UI verification and the existing runtime wind-down card
remain separate acceptance work.

## Primary contracts checked

- [Ollama CLI reference](https://docs.ollama.com/cli): native run and model listing.
- [Ollama FAQ](https://docs.ollama.com/faq#how-do-i-disable-ollama-cloud-features):
  server cloud-disable configuration and restart requirement.
- [Ollama server routes](https://github.com/ollama/ollama/blob/main/server/routes.go)
  and [API types](https://github.com/ollama/ollama/blob/main/api/types.go):
  `/api/status` and `cloud.disabled`; verified during this checkpoint, not assumed
  to be a stable API across every release.
