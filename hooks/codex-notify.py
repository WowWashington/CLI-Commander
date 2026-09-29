#!/usr/bin/env python3
"""Codex notify callback; argv[-1] is the provider's JSON event. Always silent."""
import json
import os
import sys
import urllib.request

try:
    payload = json.loads(sys.argv[-1])
    if payload.get('type') == 'agent-turn-complete' and os.environ.get('BLADE_ID'):
        body = json.dumps({'blade': os.environ['BLADE_ID'], 'event': 'CodexTurnComplete',
                           'payload': {'session_id': payload.get('thread-id'),
                                       'turn_id': payload.get('turn-id')}}).encode()
        request = urllib.request.Request(
            'http://127.0.0.1:' + os.environ.get('BLADES_PORT', '5090') + '/hook',
            data=body, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(request, timeout=3).close()
except Exception:
    pass
