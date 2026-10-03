"""Local first-run choices and credential-free CLI authentication summaries.

Only onboarding.json is written. Authentication checks invoke provider status
commands, never login, a model request, installers, or a browser. Credential
contents and account identifiers are never returned or persisted here.
"""
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import threading
import time

from . import config

SUPPORTED = ('claude', 'codex', 'gemini', 'ollama')
AUTH_CACHE_SECONDS = 30
AUTH_TIMEOUT_SECONDS = 4
_auth_cache = {}
_lock = threading.RLock()
_probe_locks = {name: threading.Lock() for name in SUPPORTED}

GUIDES = {
    'ollama': {
        'install_url': 'https://docs.ollama.com/quickstart',
        'auth_url': 'https://docs.ollama.com/cli',
        'auth_args': ['list'],
        'auth_help': 'Start Ollama on this host at 127.0.0.1:11434 with cloud disabled and prepare a local model yourself. The daemon must report cloud.disabled=true from /api/status; older servers fail closed. Enter its exact installed name in setup. Chat only: no project tools, automatic close or exact conversation resume. Checks only list models; they do not generate, pull or sign in.',
    },
    'claude': {
        'install_url': 'https://code.claude.com/docs/en/setup',
        'auth_url': 'https://code.claude.com/docs/en/authentication',
        'auth_args': ['auth', 'login'],
        'auth_help': 'On the Mac hosting CLI Commander, open Terminal and run this command. Complete the Claude sign-in in your browser, then return here and recheck. Use your existing Claude account or your organization\'s approved setup.',
    },
    'codex': {
        'install_url': 'https://learn.chatgpt.com/docs/codex/cli',
        'auth_url': 'https://learn.chatgpt.com/docs/auth',
        'auth_args': ['login'],
        'auth_help': 'On the Mac hosting CLI Commander, open Terminal and run this command. Choose Sign in with ChatGPT and complete the browser sign-in, then recheck. Codex uses your ChatGPT account; existing ChatGPT web chats are separate from these CLI sessions.',
    },
    'gemini': {
        'install_url': 'https://geminicli.com/docs/get-started/installation/',
        'auth_url': 'https://geminicli.com/docs/get-started/authentication/',
        'auth_args': [],
        'auth_help': 'On the Mac hosting CLI Commander, open Terminal and run this command. Choose Sign in with Google and finish in your browser. If Gemini already opens normally, your existing setup is in use. CLI Commander does not verify Gemini credentials in the background or change your saved account settings.',
    },
}


def preferences():
    """Malformed/missing state returns to setup, without changing other files."""
    data = config.load_json(config.path('onboarding.json'), {})
    if not isinstance(data, dict) or data.get('version') != 1:
        data = {}
    selected = data.get('enabled_providers')
    valid = (isinstance(selected, list) and bool(selected)
             and all(isinstance(name, str) and name in SUPPORTED for name in selected))
    overrides = data.get('executable_overrides', {})
    if not isinstance(overrides, dict):
        overrides = {}
    overrides = {key: value for key, value in overrides.items()
                 if key in SUPPORTED and isinstance(value, str) and os.path.isabs(value)}
    complete = data.get('completed') is True and valid
    return {'completed': complete,
            'enabled_providers': [name for name in SUPPORTED if complete and name in selected],
            'executable_overrides': overrides if complete else {},
            'ollama_model': data.get('ollama_model', '') if isinstance(data.get('ollama_model', ''), str) else ''}


def enabled(provider):
    return provider in preferences()['enabled_providers']


def _gemini_auth_presence():
    """Metadata-only check. A saved file or environment hint is not proof of login."""
    home = Path(os.environ.get('GEMINI_CLI_HOME') or str(Path.home()))
    files = [home / '.gemini/oauth_creds.json',
             Path.home() / '.config/gcloud/application_default_credentials.json']
    hint = any(path.is_file() for path in files) or any(
        bool(os.environ.get(key)) for key in ('GEMINI_API_KEY', 'GOOGLE_API_KEY',
                                              'GOOGLE_APPLICATION_CREDENTIALS', 'GOOGLE_GENAI_USE_VERTEXAI'))
    if hint:
        return {'state': 'unknown', 'detail': 'Saved authentication or provider configuration was found. Open Gemini once to verify access.'}
    return {'state': 'sign_in', 'detail': 'Open Gemini on this Mac and complete its sign-in. No saved sign-in was detected.'}


def _check_auth(provider, binary):
    from . import providers
    if not binary:
        return {'state': 'unknown', 'detail': 'Install this CLI to set up its account.'}
    if provider == 'ollama':
        from . import ollama
        return ollama.status(binary, preferences()['ollama_model'])
    if provider == 'gemini':
        return _gemini_auth_presence()
    args = [binary, 'auth', 'status', '--json'] if provider == 'claude' else [binary, 'login', 'status']
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=AUTH_TIMEOUT_SECONDS,
                                cwd=config.HOME, env=dict(providers.environment_for(binary), NO_COLOR='1'))
        if provider == 'claude':
            payload = json.loads(result.stdout)
            logged_in = payload.get('loggedIn') if isinstance(payload, dict) else None
            if logged_in is True and result.returncode == 0:
                return {'state': 'ready', 'detail': 'Claude reports a saved sign-in on this Mac.'}
            if logged_in is False:
                return {'state': 'sign_in', 'detail': 'Claude reports that sign-in is required.'}
        elif provider == 'codex':
            # CLI output may contain credential snippets. Only classify these
            # known messages; never return, log or cache the raw output.
            output = (result.stdout + '\n' + result.stderr).lower()
            if 'not logged in' in output:
                return {'state': 'sign_in', 'detail': 'Codex reports that sign-in is required.'}
            if result.returncode == 0 and ('logged in using' in output or 'logged in with' in output):
                return {'state': 'ready', 'detail': 'Codex reports a saved sign-in on this Mac.'}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return {'state': 'unknown', 'detail': 'Sign-in could not be verified automatically. Open the CLI on this Mac to check.'}


def auth_status(provider, binary, refresh=False):
    if provider not in SUPPORTED:
        return {'state': 'unknown', 'detail': 'This CLI is not supported for managed sessions.'}
    key = (provider, binary, os.environ.get('CODEX_HOME'), os.environ.get('GEMINI_CLI_HOME'), config.HOME,
           preferences()['ollama_model'] if provider == 'ollama' else None)
    with _probe_locks[provider]:
        now = time.monotonic()
        cached = _auth_cache.get(key)
        if not refresh and cached and now - cached[0] < AUTH_CACHE_SECONDS:
            return dict(cached[1])
        summary = _check_auth(provider, binary)
        _auth_cache[key] = (time.monotonic(), dict(summary))
        return summary


def status(refresh=False):
    from . import providers
    choices = preferences()
    entries = []
    for item in providers.available():
        name = item['id']
        binary = providers.executable(name)
        guide = GUIDES[name]
        entries.append({**item, 'executable': binary,
                        'executables': providers.discover(name),
                        'auth': auth_status(name, binary, refresh=refresh),
                        'install_url': guide['install_url'], 'auth_url': guide['auth_url'],
                        'auth_command': shlex.join([binary or name, *guide['auth_args']]),
                        'auth_help': guide['auth_help']})
    other = []
    for name, label in (('copilot', 'GitHub Copilot CLI'),):
        detected = providers.discover(name)
        if detected:
            other.append({'id': name, 'label': label, 'available': True, 'supported': False,
                          'detail': 'Detected on this Mac. Managed sessions and safe resume are not supported yet.'})
    return {**choices, 'providers': entries, 'other_clis': other,
            'system': {'platform': 'macOS' if platform.system() == 'Darwin' else platform.system(),
                       'tmux_available': bool(shutil.which('tmux')), 'port': config.PORT}}


def configure(payload):
    """Save validated choices. Enabling a CLI does not log in or launch it."""
    from . import providers
    if not isinstance(payload, dict):
        raise ValueError('Setup choices must be an object')
    selected = payload.get('enabled_providers')
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(name, str) or name not in SUPPORTED for name in selected)):
        raise ValueError('Choose at least one supported CLI: Claude, Codex, Gemini, or Ollama')
    overrides = payload.get('executable_overrides', {})
    if not isinstance(overrides, dict) or any(key not in SUPPORTED for key in overrides):
        raise ValueError('Executable choices must name supported CLIs')
    selected = [name for name in SUPPORTED if name in selected]
    with _lock:
        # Preserve a previous explicit installation choice when the UI only
        # changes enablement. A reset is the separate action that clears it.
        previous = preferences()
        saved = previous['executable_overrides']
        model = payload.get('ollama_model', previous['ollama_model'])
        if 'ollama' in selected:
            from . import ollama
            model = ollama.validate_model(model)
        elif not isinstance(model, str):
            raise ValueError('Ollama model must be text')
        for name, chosen in overrides.items():
            if chosen is None:
                saved.pop(name, None)
            elif isinstance(chosen, str) and any(item['path'] == chosen for item in providers.discover(name)):
                saved[name] = chosen
            else:
                raise ValueError('Choose an executable found by CLI discovery')
        for name in selected:
            candidates = providers.discover(name)
            chosen = saved.get(name)
            if not candidates or (chosen and all(item['path'] != chosen for item in candidates)):
                raise ValueError(f'{name.capitalize()} is not available on this Mac; install it and recheck')
        config.save_json(config.path('onboarding.json'),
                         {'version': 1, 'completed': True, 'enabled_providers': selected,
                          'executable_overrides': saved, 'ollama_model': model})
    return status()


def reset():
    """Clear only setup choices; sessions, projects, archives and accounts remain."""
    with _lock:
        try:
            os.unlink(config.path('onboarding.json'))
        except FileNotFoundError:
            pass
    return status()
