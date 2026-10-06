"""Code locations: parent folders whose child folders are offered as projects.

Saved in server.json as ``project_roots``. Older installs with a single
``projects_root`` keep working; never-configured hosts fall back to ~/Projects.
BLADES_PROJECTS_ROOT overrides the saved list. Listing is read-only: folders
are never created, moved or modified here.
"""
import os
import time

from . import config, projects

MAX_LOCATIONS = 20
MAX_CHILDREN = 300
SUGGESTED = ("Projects", "projects", "Code", "code", "Developer", "dev", "src", "repos",
             "git", "GitHub", "Documents/GitHub", "Documents/Projects", "workspace", "Sites")
DEFAULT_IGNORE = ["node_modules", "venv", "__pycache__"]


def env_override():
    value = os.environ.get("BLADES_PROJECTS_ROOT")
    return os.path.realpath(os.path.expanduser(value)) if value else ""


def configured():
    """Saved locations, or the legacy/default single folder when never saved."""
    override = env_override()
    if override:
        return [override]
    saved = config.server_settings()
    if isinstance(saved.get("project_roots"), list):
        return [p for p in saved["project_roots"] if isinstance(p, str) and os.path.isabs(p)]
    legacy = saved.get("projects_root")
    if isinstance(legacy, str) and os.path.isabs(legacy):
        return [legacy]
    default = os.path.join(config.HOME, "Projects")
    return [default] if os.path.isdir(default) else []


def validate(paths):
    """Return normalized, de-duplicated absolute folders or raise ValueError."""
    if not isinstance(paths, list) or len(paths) > MAX_LOCATIONS:
        raise ValueError(f"Choose at most {MAX_LOCATIONS} code locations")
    out, seen = [], set()
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
            raise ValueError("Each code location must be a folder path")
        expanded = os.path.expanduser(raw.strip())
        if not os.path.isabs(expanded):
            raise ValueError("Use an absolute folder path or a path starting with ~")
        folder = os.path.realpath(expanded)
        if not os.path.isdir(folder):
            raise ValueError(f"{raw.strip()} is not an existing folder on this Mac")
        if folder == os.path.sep:
            raise ValueError("Choose a folder that holds your projects, not the whole disk")
        if projects.folder_identity(folder) not in seen:
            seen.add(projects.folder_identity(folder))
            out.append(folder)
    return out


def ignore_list():
    return set(config.load_json(config.path("ignore.json"), DEFAULT_IGNORE))


def children(root, known=None):
    """Candidate project folders directly inside one location."""
    known = known if known is not None else projects.registered_identities()
    ignored = ignore_list()
    try:
        names = sorted(os.listdir(root), key=str.lower)
    except OSError:
        return []
    out = []
    for name in names:
        if len(out) >= MAX_CHILDREN:
            break
        full = os.path.join(root, name)
        if name.startswith(".") or name in ignored or not os.path.isdir(full):
            continue
        out.append({"name": name, "path": full, "root": root,
                    "registered": projects.folder_identity(full) in known,
                    "is_git": os.path.exists(os.path.join(full, ".git")),
                    "has_state": os.path.exists(os.path.join(full, "STATE.md"))})
    return out


def summary(root, known=None):
    items = children(root, known)
    return {"path": root, "exists": os.path.isdir(root), "folders": len(items),
            "git": sum(1 for i in items if i["is_git"])}


def suggestions(exclude=()):
    """Common code folders that exist on this Mac, excluding ones already chosen."""
    seen, out = {projects.folder_identity(p) for p in exclude}, []
    for rel in SUGGESTED:
        full = os.path.join(config.HOME, rel)
        if os.path.isdir(full) and projects.folder_identity(full) not in seen:
            seen.add(projects.folder_identity(full))
            out.append(os.path.realpath(full))
    return out


def status():
    known = projects.registered_identities()
    current = configured()
    return {"locations": [summary(p, known) for p in current],
            "suggestions": [summary(p, known) for p in suggestions(current)],
            "env_override": bool(env_override()), "home": os.path.realpath(config.HOME)}


def preview(paths):
    """Unregistered child folders for proposed locations, without saving them."""
    known = projects.registered_identities()
    return [item for root in validate(paths) for item in children(root, known) if not item["registered"]]


def unregistered():
    known = projects.registered_identities()
    out = []
    for root in configured():
        for item in children(root, known):
            if item["registered"]:
                continue
            full = item["path"]
            git = projects.git_info(full)
            try:
                entries = os.listdir(full)
                mtime = max([os.stat(full).st_mtime] + [os.stat(os.path.join(full, f)).st_mtime
                                                       for f in entries[:100]
                                                       if os.path.exists(os.path.join(full, f))])
            except OSError:
                entries, mtime = [], 0
            out.append({**item, "is_git": git["is_git"],
                        "remote": git["remote"]["url"] if git["remote"] else None,
                        "files": len(entries),
                        "modified": time.strftime("%Y-%m-%d", time.localtime(mtime))})
    return out


def contains(path):
    """The configured location directly containing ``path``, or None."""
    parent = projects.folder_identity(os.path.dirname(os.path.realpath(path)))
    return next((root for root in configured() if projects.folder_identity(root) == parent), None)
