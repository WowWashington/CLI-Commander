"""Project audit: unregistered folders under ~/Projects, and the health of
every registered project (path, git, remote, STATE.md age, pending handoff).
Run nightly after wind-downs; also available live in the dashboard."""
import os
import re
import time

from . import config, projects, prompts

REPORT_DIR = config.path("reports")
DEFAULT_IGNORE = ["node_modules", "venv", "__pycache__"]
STALE_STATE_DAYS = 14


def project_root():
    return os.path.expanduser(os.environ.get("BLADES_PROJECTS_ROOT") or
                             config.server_settings().get("projects_root") or os.path.join(config.HOME, "Projects"))


def ignore_list():
    return set(config.load_json(config.path("ignore.json"), DEFAULT_IGNORE))


def add_ignore(name):
    ig = ignore_list(); ig.add(name)
    config.save_json(config.path("ignore.json"), sorted(ig))


def unregistered():
    root = project_root()
    known = {p["path"].rstrip("/") for p in projects.parse_projects_md()}
    ig = ignore_list()
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for n in names:
        full = os.path.join(root, n)
        if n.startswith(".") or n in ig or not os.path.isdir(full) or full in known:
            continue
        g = projects.git_info(full)
        try:
            mtime = max([os.stat(full).st_mtime] + [os.stat(os.path.join(full, f)).st_mtime
                                                   for f in os.listdir(full)[:100]
                                                   if os.path.exists(os.path.join(full, f))])
        except OSError:
            mtime = 0
        out.append({"name": n, "path": full, "is_git": g["is_git"],
                    "remote": g["remote"]["url"] if g["remote"] else None,
                    "has_state": os.path.exists(os.path.join(full, "STATE.md")),
                    "files": len(os.listdir(full)) if os.path.isdir(full) else 0,
                    "modified": time.strftime("%Y-%m-%d", time.localtime(mtime))})
    return out


def register(name, keywords=None):
    """Register a discovered child folder without editing external indexes."""
    if not isinstance(name, str) or not name or name in (".", "..") or os.path.basename(name) != name:
        raise RuntimeError("Choose a discovered project folder")
    try:
        return projects.add_project(os.path.join(project_root(), name))
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def report():
    now = time.time()
    r = {"generated": time.strftime("%Y-%m-%d %H:%M"), "unregistered": unregistered(),
         "missing": [], "dirty": [], "unpushed": [], "no_remote": [], "stale_state": [],
         "no_state": [], "handoff": []}
    for p in projects.parse_projects_md():
        if re.search(r"retired|archived", p["status"], re.I):
            continue
        if not os.path.isdir(p["path"]):
            r["missing"].append({"name": p["name"], "path": p["path"]}); continue
        g = projects.git_info(p["path"])
        item = {"name": p["name"], "slug": p["slug"]}
        if g["is_git"]:
            if g["dirty"]:
                r["dirty"].append({**item, "files": g["dirty"]})
            if g["ahead"]:
                r["unpushed"].append({**item, "commits": g["ahead"]})
            if not g["remote"]:
                r["no_remote"].append(item)
        st = os.path.join(p["path"], "STATE.md")
        if not os.path.exists(st):
            r["no_state"].append(item)
        elif g["is_git"] and g["last_commit"]:
            try:
                commit_ts = time.mktime(time.strptime(g["last_commit"], "%Y-%m-%d"))
                age_days = (commit_ts - os.stat(st).st_mtime) / 86400
                if age_days > STALE_STATE_DAYS:
                    r["stale_state"].append({**item, "state_md": time.strftime("%Y-%m-%d", time.localtime(os.stat(st).st_mtime)), "last_commit": g["last_commit"]})
            except ValueError:
                pass
        if prompts.read_handoff(p["slug"]):
            r["handoff"].append(item)
    r["counts"] = {k: len(v) for k, v in r.items() if isinstance(v, list)}
    return r


def write_report():
    r = report()
    os.makedirs(REPORT_DIR, exist_ok=True)
    day = time.strftime("%Y-%m-%d")
    lines = [f"# Projects audit {r['generated']}", ""]
    def sect(title, items, fmt):
        lines.append(f"## {title} ({len(items)})")
        lines.extend(fmt(i) for i in items) if items else lines.append("- none")
        lines.append("")
    sect("Unregistered folders under ~/Projects", r["unregistered"],
         lambda i: f"- {i['name']} — {'git' if i['is_git'] else 'no git'}{', remote ' + i['remote'] if i['remote'] else ''}, {'STATE.md' if i['has_state'] else 'no STATE.md'}, modified {i['modified']}")
    sect("Registered but path missing", r["missing"], lambda i: f"- {i['name']} ({i['path']})")
    sect("Uncommitted changes", r["dirty"], lambda i: f"- {i['name']}: {i['files']} files")
    sect("Unpushed commits", r["unpushed"], lambda i: f"- {i['name']}: {i['commits']} commits")
    sect("No GitHub remote", r["no_remote"], lambda i: f"- {i['name']}")
    sect(f"STATE.md older than last commit by >{STALE_STATE_DAYS} days", r["stale_state"],
         lambda i: f"- {i['name']}: STATE.md {i['state_md']}, last commit {i['last_commit']}")
    sect("No STATE.md", r["no_state"], lambda i: f"- {i['name']}")
    sect("Pending handoff notes", r["handoff"], lambda i: f"- {i['name']}")
    path = os.path.join(REPORT_DIR, f"projects-audit-{day}.md")
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    latest = os.path.join(REPORT_DIR, "projects-audit-latest.md")
    try:
        if os.path.islink(latest) or os.path.exists(latest):
            os.remove(latest)
        os.symlink(path, latest)
    except OSError:
        pass
    r["report_path"] = path
    return r
