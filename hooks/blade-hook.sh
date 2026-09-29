#!/bin/bash
# CLI Commander hook relay. Installed into ~/.claude/settings.json for
# SessionStart, UserPromptSubmit, Stop, Notification, SessionEnd.
# Silent and always exit 0: hook stdout would be injected into Claude's context.
EVENT="$1"
INPUT="$(cat 2>/dev/null)"
[ -z "$INPUT" ] && INPUT="null"
PORT="${BLADES_PORT:-5090}"
python3 - "${BLADE_ID:-}" "$EVENT" "$PORT" <<'PY' "$INPUT" >/dev/null 2>&1 || true
import json, sys, urllib.request
blade, event, port, raw = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    payload = json.loads(raw)
except Exception:
    payload = {"raw": raw[:500]}
body = json.dumps({"blade": blade, "event": event, "payload": payload}).encode()
path = "/hook" if blade else "/hook/legacy"
req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                             headers={"Content-Type": "application/json"})
try:
    urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=2).close()
except Exception:
    pass
PY
exit 0
