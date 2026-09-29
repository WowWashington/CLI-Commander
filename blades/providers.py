"""Local CLI providers. Credentials remain in the provider's normal local store."""
import json
import re
from contextlib import closing
import os
import shutil
import sqlite3
from pathlib import Path
from . import config

PROVIDER_IDS = ('claude', 'codex', 'gemini')


def environment_for(binary):
    """Child environment with the selected installation's runtime on PATH.

    nvm/fnm CLIs commonly use ``#!/usr/bin/env node``. Keep their sibling Node
    ahead of launchd's PATH without sourcing shell profiles or switching the
    parent process environment. Callers writing launch scripts should export
    only this PATH value, never serialize the rest of this environment.
    """
    environment = os.environ.copy()
    if binary:
        folder = str(Path(binary).absolute().parent)
        current = environment.get('PATH', os.defpath).split(os.pathsep)
        environment['PATH'] = os.pathsep.join([folder, *(entry for entry in current if entry != folder)])
    return environment


def discover(provider):
    """Find executables in known install locations without running installers."""
    values = {'claude': config.CLAUDE_BIN, 'codex': config.CODEX_BIN,
              'gemini': config.GEMINI_BIN}
    if provider not in (*PROVIDER_IDS, 'copilot', 'ollama'):
        return []
    home = Path.home()
    candidates = [(values.get(provider, provider), 'Configured command'), (provider, 'PATH')]
    for folder, source in (
        (home / '.local/bin', 'User installation'),
        (Path('/opt/homebrew/bin'), 'Homebrew'),
        (Path('/usr/local/bin'), 'Local installation'),
        (home / '.npm-global/bin', 'npm'), (home / '.npm/bin', 'npm'),
        (home / '.local/share/npm/bin', 'npm'), (home / '.volta/bin', 'Volta'),
        (home / 'Library/pnpm', 'pnpm'), (home / '.local/share/pnpm', 'pnpm'),
    ):
        candidates.append((str(folder / provider), source))
    # A launchd service does not inherit interactive Node manager PATH entries.
    # Search only their known executable folders, never arbitrary project files.
    for pattern, source in (('.nvm/versions/node/*/bin', 'nvm'),
                            ('.local/share/fnm/node-versions/*/installation/bin', 'fnm')):
        for folder in sorted(home.glob(pattern), reverse=True)[:20]:
            candidates.append((str(folder / provider), source))
    if provider == 'codex':
        for applications in (Path('/Applications'), home / 'Applications'):
            for app in ('ChatGPT.app', 'Codex.app'):
                candidates.append((str(applications / app / 'Contents/Resources/codex'), app.removesuffix('.app')))
    if provider == 'ollama':
        candidates.append(('/Applications/Ollama.app/Contents/Resources/ollama', 'Ollama'))
    found, seen = [], set()
    for value, source in candidates:
        binary = shutil.which(value) if value else None
        if not binary or not Path(binary).is_file():
            continue
        path = str(Path(binary).absolute())
        real = os.path.realpath(path)
        if real not in seen:
            seen.add(real)
            found.append({'path': path, 'source': source})
    return found


def executable(provider):
    if provider not in PROVIDER_IDS:
        return None
    from . import onboarding
    chosen = onboarding.preferences()['executable_overrides'].get(provider)
    candidates = discover(provider)
    if chosen:
        # Do not silently switch accounts/configuration to another installation
        # when a specifically selected CLI has disappeared.
        return chosen if any(item['path'] == chosen for item in candidates) else None
    return candidates[0]['path'] if candidates else None


def available():
    from . import onboarding
    enabled = onboarding.preferences()['enabled_providers']
    items = [
        {'id': 'claude', 'label': 'Claude', 'available': bool(executable('claude')),
         'detail': 'Claude Code · your local account'},
        {'id': 'codex', 'label': 'Codex', 'available': bool(executable('codex')),
         'detail': 'OpenAI Codex CLI · sign in with ChatGPT using codex login'},
        {'id': 'gemini', 'label': 'Gemini', 'available': bool(executable('gemini')),
         'detail': 'Google Gemini CLI · sign in on first launch'},
    ]
    for item in items:
        item['enabled'] = item['id'] in enabled
    return items


def transcript(provider, session_id, cwd=None):
    """Find only the exact provider session, never the most recent conversation."""
    if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", session_id):
        return None
    if provider == 'codex':
        home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
        for db in sorted(home.glob('state_*.sqlite'), reverse=True):
            try:
                with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=1)) as c:
                    row = c.execute('SELECT rollout_path FROM threads WHERE id=?', (session_id,)).fetchone()
                    if row and row[0] and Path(row[0]).is_file():
                        return row[0]
            except sqlite3.Error:
                continue
        # Older Codex stores rollouts directly as JSONL. Match the exact UUID.
        for folder in ('sessions', 'archived_sessions'):
            for file in (home / folder).glob(f'**/*{session_id}.jsonl'):
                return str(file)
    elif provider == 'claude':
        for file in (Path.home() / '.claude' / 'projects').glob(f'*/{session_id}.jsonl'):
            if file.is_file():
                return str(file)
    elif provider == 'gemini':
        import hashlib
        home = Path(os.environ.get('GEMINI_CLI_HOME') or str(Path.home())) / '.gemini' / 'tmp'
        folders = [home / hashlib.sha256(str(Path(cwd).resolve()).encode()).hexdigest()] if cwd else home.glob('*')
        for folder in folders:
            for file in (folder / 'chats').glob(f'session-*-{session_id[:8]}.json'):
                try:
                    with file.open(encoding='utf-8') as stream:
                        data = json.load(stream)
                    if (isinstance(data, dict) and data.get('sessionId') == session_id
                            and data.get('projectHash') == folder.name
                            and isinstance(data.get('messages'), list)):
                        return str(file)
                except (OSError, ValueError):
                    continue
    return None


def codex_completion_transcript(session_id, cwd):
    """Prove a callback belongs to a top-level local CLI, never an internal agent."""
    if not isinstance(session_id, str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', session_id):
        return None
    if not isinstance(cwd, str) or not cwd:
        return None

    def matches(record):
        recorded_cwd = record.get('cwd')
        return (isinstance(recorded_cwd, str)
                and Path(recorded_cwd).resolve() == Path(cwd).resolve()
                and record.get('source') == 'cli'
                and record.get('thread_source', 'user') == 'user'
                and not record.get('agent_path') and not record.get('agent_role'))

    home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    for db in sorted(home.glob('state_*.sqlite'), reverse=True):
        try:
            with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=1)) as c:
                c.row_factory = sqlite3.Row
                row = c.execute('SELECT * FROM threads WHERE id=?', (session_id,)).fetchone()
                if row:
                    record = dict(row)
                    path = record.get('rollout_path')
                    return str(path) if matches(record) and path and Path(path).is_file() else None
        except sqlite3.Error:
            continue
    path = transcript('codex', session_id)
    if path:
        try:
            with open(path, encoding='utf-8') as stream:
                for _ in range(32):
                    line = stream.readline(1024 * 1024)
                    if not line:
                        break
                    record = json.loads(line)
                    if record.get('type') == 'session_meta':
                        metadata = record.get('payload') or {}
                        return path if metadata.get('id') == session_id and matches(metadata) else None
        except (OSError, ValueError, AttributeError):
            pass
    return None
