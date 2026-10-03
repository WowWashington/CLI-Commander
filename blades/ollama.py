"""Minimal native Ollama chat launcher; no application configuration imports.

Only list installed models during checks. No pull, login, integration setup,
completion hook or automatic recovery is implemented here.
"""
import argparse
import http.client
import json
import os
import re
import subprocess
import sys

HOST = 'http://127.0.0.1:11434'


def validate_model(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}', value):
        raise ValueError('Choose an exact installed Ollama model name, including its tag')
    if "cloud" in value.lower():
        raise ValueError("Cloud models are not supported by the local Ollama chat provider")
    return value


def environment(binary):
    env = os.environ.copy()
    env['OLLAMA_HOST'] = HOST
    env['OLLAMA_NO_CLOUD'] = '1'
    env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
    folder = os.path.dirname(os.path.abspath(binary))
    env['PATH'] = folder + os.pathsep + env.get('PATH', os.defpath)
    return env


def cloud_disabled():
    """Require the daemon's own policy, not just a client environment variable.

    This endpoint is experimental upstream. Missing/changed evidence blocks
    launch. HTTPConnection uses the fixed loopback address without proxies or
    redirect following. No credentials, model prompt or account endpoint.
    """
    connection = http.client.HTTPConnection('127.0.0.1', 11434, timeout=2)
    try:
        connection.request('GET', '/api/status')
        response = connection.getresponse()
        if response.status != 200:
            return False
        raw = response.read(16385)
        if len(raw) > 16384:
            return False
        data = json.loads(raw)
        cloud = data.get('cloud') if isinstance(data, dict) else None
        return isinstance(cloud, dict) and cloud.get('disabled') is True
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def status(binary, model):
    if not binary:
        return {'state': 'unavailable', 'detail': 'Install Ollama on this host, then recheck.'}
    try:
        validate_model(model)
    except ValueError as exc:
        return {'state': 'unavailable', 'detail': str(exc)}
    if not cloud_disabled():
        return {'state': 'unavailable', 'detail': 'Local-only mode is not confirmed. Disable Ollama cloud on its server, restart it, and recheck; a server with /api/status support is required.'}
    try:
        result = subprocess.run([binary, 'list'], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=4,
                                env=environment(binary))
    except (OSError, subprocess.TimeoutExpired):
        return {'state': 'unavailable', 'detail': 'Ollama is unavailable at 127.0.0.1:11434. Start its local service, then recheck.'}
    if result.returncode != 0:
        return {'state': 'unavailable', 'detail': 'Ollama did not list its models. Check the local service, then recheck.'}
    lines = result.stdout.splitlines()
    if not lines or not lines[0].split() or lines[0].split()[0] != 'NAME':
        return {'state': 'unavailable', 'detail': 'Ollama returned an unrecognized model list. Check its CLI version.'}
    models = {line.split()[0] for line in lines[1:] if line.split()}
    if model not in models:
        return {'state': 'unavailable', 'detail': 'That exact model is not installed. Prepare it in Ollama first; Commander does not pull models.'}
    return {'state': 'ready', 'detail': 'Cloud disabled; model listed by the local Ollama service. Chat only; no automatic close or exact resume.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Explicit native Ollama chat; local-only readiness checks and manual lifecycle')
    parser.add_argument('--binary', required=True)
    parser.add_argument('--model', required=True)
    args = parser.parse_args(argv)
    check = status(args.binary, args.model)
    if check['state'] != 'ready':
        print(check['detail'], file=sys.stderr)
        return 2
    # Recheck immediately before exec. The daemon/model can still change after
    # this point; native CLI failures remain visible in the retained pane.
    try:
        os.execve(args.binary, [args.binary, 'run', args.model], environment(args.binary))
    except OSError:
        print('Ollama could not start. Check the selected installation.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
