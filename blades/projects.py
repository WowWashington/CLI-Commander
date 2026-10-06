"""Private project registry, with optional legacy projects.md compatibility."""
import os
import re
import subprocess
import threading
import time

from . import config

_cache = {}
_cache_lock = threading.Lock()
_registry_lock = threading.RLock()
GIT_TTL = 20  # seconds


def sh(cmd, cwd=None, timeout=8):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                           timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def _legacy_projects():
    rows = []
    try:
        with open(config.PROJECTS_MD, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return rows
    for line in text.splitlines():
        m = re.match(r"\|\s*(\d+)\s*\|([^|]+)\|([^|]+)\|\s*`?([^`|]+?)`?\s*\|"
                     r"[^|]*\|[^|]*\|([^|]*)\|([^|]*)\|", line)
        if not m:
            continue
        name = m.group(2).strip()
        rows.append({
            "num": int(m.group(1)),
            "name": name,
            "slug": config.slugify(name),
            "keywords": m.group(3).strip(),
            "path": os.path.expanduser(m.group(4).strip()),
            "github_note": m.group(5).strip(),
            "status": m.group(6).strip(),
        })
    return rows


def parse_projects_md():
    """Return registered projects; retain the legacy function for existing callers."""
    rows = _legacy_projects()
    paths = {os.path.realpath(p["path"]) for p in rows}
    slugs = {p["slug"] for p in rows}
    for item in config.load_json(config.path("projects.json"), []):
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("name", "path", "slug")):
            continue
        if os.path.realpath(item["path"]) in paths or item["slug"] in slugs:
            continue
        rows.append({"num": len(rows) + 1, "keywords": "", "github_note": "", "status": "Active", **item})
        paths.add(os.path.realpath(item["path"]))
        slugs.add(item["slug"])
    return rows


def folder_identity(path):
    """Same folder despite case-insensitive names or symlinks."""
    try:
        st = os.stat(path)
        return (st.st_dev, st.st_ino)
    except OSError:
        return os.path.realpath(path)


def registered_identities():
    return {folder_identity(p["path"]) for p in parse_projects_md()}


def add_project(folder, name=None):
    if not isinstance(folder, str) or not folder.strip() or '\x00' in folder:
        raise ValueError("Choose an existing project folder")
    expanded = os.path.expanduser(folder.strip())
    if not os.path.isabs(expanded):
        raise ValueError("Use an absolute folder path or a path starting with ~")
    folder = os.path.realpath(expanded)
    if not os.path.isdir(folder):
        raise ValueError("That project folder does not exist on the host")
    if name is not None and (not isinstance(name, str) or len(name) > 120 or any(ord(c) < 32 for c in name)):
        raise ValueError("Project name must be text of at most 120 characters")
    name = (name or "").strip() or os.path.basename(folder) or "Workspace"
    with _registry_lock:
        rows = parse_projects_md()
        if folder_identity(folder) in {folder_identity(p["path"]) for p in rows}:
            raise ValueError("That folder is already registered")
        base = config.slugify(name)
        slug, suffix = base, 2
        taken = {p["slug"] for p in rows} | {"shell-commands"}
        while slug in taken:
            slug, suffix = f"{base}-{suffix}", suffix + 1
        item = {"name": name, "slug": slug, "path": folder}
        saved = config.load_json(config.path("projects.json"), [])
        config.save_json(config.path("projects.json"), [*saved, item])
        invalidate()
        return item


STATE_TEMPLATE = """# {name} — Project State

## Purpose

(Describe what this project is for.)

## Current status

**Last updated**: {date}
**State**: New project, created from CLI Commander.
**Next steps**: Define the goal and first tasks.
"""


def create_project(name, parent=None, git=True):
    """Create a new folder (default: the first code location) with a starter
    STATE.md and optional git repository, then register it. Only the new
    folder is ever written; it is removed again if setup fails."""
    from . import locations
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Enter a project name")
    name = name.strip()
    if len(name) > 120 or any(ord(c) < 32 for c in name):
        raise ValueError("Project name must be text of at most 120 characters")
    folder_name = re.sub(r"[/\\:]+", "-", name).strip()
    if not folder_name or folder_name.startswith(".") or folder_name in ("..", "."):
        raise ValueError("That name cannot be used as a folder name")
    if parent is not None and (not isinstance(parent, str) or "\x00" in parent):
        raise ValueError("Choose a location for the new folder")
    default = (locations.configured() or [os.path.join(config.HOME, "Projects")])[0]
    parent = os.path.expanduser((parent or "").strip() or default)
    if not os.path.isabs(parent):
        raise ValueError("Use an absolute location or a path starting with ~")
    parent = os.path.realpath(parent)
    if not os.path.isdir(parent):
        raise ValueError("That location does not exist on the host")
    folder = os.path.join(parent, folder_name)
    with _registry_lock:
        try:
            os.mkdir(folder)
        except FileExistsError:
            raise ValueError("A folder with that name already exists; add it as an existing folder instead")
        except OSError as exc:
            raise ValueError(f"Could not create the folder: {exc.strerror or exc}")
        try:
            with open(os.path.join(folder, "STATE.md"), "x", encoding="utf-8") as f:
                f.write(STATE_TEMPLATE.format(name=name, date=time.strftime("%Y-%m-%d")))
            if git:
                subprocess.run(["git", "init", "-q"], cwd=folder, capture_output=True, timeout=15, check=True)
            return add_project(folder, name)
        except Exception as exc:
            import shutil
            shutil.rmtree(folder, ignore_errors=True)
            if isinstance(exc, ValueError):
                raise
            raise ValueError(f"Could not set up the project: {exc}")


def remove_project(slug):
    """Unregister a project. Its folder, notes and archives are left untouched."""
    with _registry_lock:
        saved = config.load_json(config.path("projects.json"), [])
        kept = [item for item in saved if not (isinstance(item, dict) and item.get("slug") == slug)]
        if len(kept) == len(saved):
            if any(p["slug"] == slug for p in _legacy_projects()):
                raise ValueError("This project comes from your projects.md index; edit that file or hide the project instead")
            raise LookupError("unknown project")
        config.save_json(config.path("projects.json"), kept)
        lay = config.layout()
        config.save_layout({"order": [s for s in lay.get("order", []) if s != slug],
                            "hidden": [s for s in lay.get("hidden", []) if s != slug]})
        invalidate()


def github_from_remote(url):
    if not url:
        return None
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", url)
    if not m:
        return None
    return {"owner": m.group(1), "repo": m.group(2),
            "url": f"https://github.com/{m.group(1)}/{m.group(2)}"}


def git_info(path):
    """Cached git facts for a project path."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(path)
        if hit and now - hit["ts"] < GIT_TTL:
            return hit["data"]
    data = {"is_git": False, "dirty": 0, "ahead": 0, "behind": 0,
            "branch": "", "last_commit": "", "remote": None, "state": "none"}
    if os.path.exists(os.path.join(path, ".git")):
        data["is_git"] = True
        data["branch"] = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
        data["last_commit"] = sh(["git", "log", "-1", "--format=%cd",
                                  "--date=format:%Y-%m-%d"], cwd=path)
        data["dirty"] = len([l for l in sh(["git", "status", "--porcelain"],
                                           cwd=path).splitlines() if l.strip()])
        lr = sh(["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"],
                cwd=path)
        if lr:
            try:
                data["behind"], data["ahead"] = (int(x) for x in lr.split())
            except ValueError:
                pass
        data["remote"] = github_from_remote(
            sh(["git", "remote", "get-url", "origin"], cwd=path))
        if data["dirty"]:
            data["state"] = "dirty"
        elif data["ahead"]:
            data["state"] = "unpushed"
        elif not data["remote"]:
            data["state"] = "local"
        else:
            data["state"] = "clean"
    with _cache_lock:
        _cache[path] = {"ts": now, "data": data}
    return data


def invalidate(path=None):
    with _cache_lock:
        if path:
            _cache.pop(path, None)
        else:
            _cache.clear()


_status_cache = {}
STATUS_PATTERNS = [
    r"^\*\*State\*\*:\s*(.+)$",
    r"^>\s*\*\*Status:?\*\*:?\s*(.+)$",
    r"^\*\*Status\*\*:?\s*(.+)$",
    r"^Status:\s*(.+)$",
]


def status_line(path, fallback=""):
    """One-line status from STATE.md (State/Status line), else projects.md status."""
    st = os.path.join(path, "STATE.md")
    try:
        mtime = os.stat(st).st_mtime
    except OSError:
        return fallback, None
    hit = _status_cache.get(st)
    if hit and hit[0] == mtime:
        return hit[1], time.strftime("%Y-%m-%d", time.localtime(mtime))
    line = ""
    try:
        with open(st, encoding="utf-8") as f:
            text = f.read(20000)
        for pat in STATUS_PATTERNS:
            m = re.search(pat, text, re.M)
            if m:
                line = m.group(1).strip()
                break
    except OSError:
        pass
    line = re.sub(r"[*`_]", "", line).strip()
    if len(line) > 160:
        line = line[:157].rstrip() + "..."
    line = line or fallback
    _status_cache[st] = (mtime, line)
    return line, time.strftime("%Y-%m-%d", time.localtime(mtime))


def all_projects(include_hidden=True):
    lay = config.layout()
    order = {slug: i for i, slug in enumerate(lay.get("order", []))}
    hidden = set(lay.get("hidden", []))
    rows = []
    for p in parse_projects_md():
        p["exists"] = os.path.isdir(p["path"])
        p["hidden"] = p["slug"] in hidden
        if p["hidden"] and not include_hidden:
            continue
        p["git"] = git_info(p["path"]) if p["exists"] else None
        st = os.path.join(p["path"], "STATE.md")
        p["state_md"] = (time.strftime("%Y-%m-%d", time.localtime(os.stat(st).st_mtime))
                         if os.path.exists(st) else None)
        p["handoff"] = os.path.exists(config.path("handoff", f"{p['slug']}.md"))
        if p["exists"]:
            p["status_line"], _ = status_line(p["path"], p["status"])
            dates = [d for d in (p["state_md"], p["git"]["last_commit"] if p["git"] else None) if d]
            p["modified"] = max(dates) if dates else None
        else:
            p["status_line"], p["modified"] = "path missing", None
        cfg = config.project_config(p["slug"])
        p["has_config"] = bool(cfg.get("prompt") or cfg.get("notes") or cfg.get("related"))
        p["sort"] = (0, order[p["slug"]]) if p["slug"] in order else (1, p["num"])
        rows.append(p)
    rows.sort(key=lambda r: r["sort"])
    for r in rows:
        r.pop("sort", None)
    return rows


def by_slug(slug):
    if slug == "shell-commands":
        from . import quick
        return quick.pseudo_project()
    for p in parse_projects_md():
        if p["slug"] == slug:
            p["exists"] = os.path.isdir(p["path"])
            return p
    return None
