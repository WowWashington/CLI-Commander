#!/usr/bin/env python3
"""Relay Gemini lifecycle metadata to loopback without modifying hook decisions."""
import json
import os
import sys
import urllib.request

EVENTS = {"SessionStart", "BeforeAgent", "AfterAgent", "BeforeTool", "AfterTool",
          "BeforeModel", "Notification", "SessionEnd"}


def main():
    try:
        event = sys.argv[1] if len(sys.argv) > 1 else ""
        blade = os.environ.get("BLADE_ID")
        if event not in EVENTS or not blade:
            return
        native = json.load(sys.stdin)
        if not isinstance(native, dict) or native.get("hook_event_name") != event:
            return
        fields = ("session_id", "transcript_path", "cwd", "hook_event_name", "timestamp",
                  "source", "reason", "notification_type", "stop_hook_active", "tool_name", "tool_use_id")
        payload = {key: native[key] for key in fields if key in native}
        # A background flag is relevant to safe closure; command text and model
        # requests/responses are private and unnecessary for session monitoring.
        tool_input = native.get("tool_input")
        if isinstance(tool_input, dict):
            payload["tool_input"] = {key: tool_input[key] for key in ("run_in_background", "is_background")
                                     if isinstance(tool_input.get(key), bool)}
        if native.get("error"):
            payload["error"] = True
        body = json.dumps({"blade": blade, "event": "Gemini" + event, "payload": payload}).encode()
        port = int(os.environ.get("BLADES_PORT", "5090"))
        if not 1 <= port <= 65535:
            return
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/hook", data=body,
            headers={"Content-Type": "application/json"})
        # Ignore proxy environment variables for this machine-local callback.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open(request, timeout=3).close()
    except Exception:
        pass
    finally:
        # Gemini parses stdout as hook JSON. Monitoring never changes approvals.
        print("{}")


if __name__ == "__main__":
    main()
