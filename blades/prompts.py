"""Render the opening prompt for a blade and write/consume handoff notes."""
import hashlib
import os
import threading
import time
import uuid

from . import config, projects

_handoff_lock = threading.RLock()


def _related_block(cfg, all_by_slug):
    out = []
    for slug in cfg.get("related", []):
        other = all_by_slug.get(slug)
        ocfg = config.project_config(slug)
        if not other:
            continue
        line = f"- {other['name']} ({other['path']})"
        if ocfg.get("notes"):
            line += f": {ocfg['notes'].strip()}"
        out.append(line)
    if not out:
        return ""
    return "\n\nRelated projects this one interacts with:\n" + "\n".join(out) + "\n"


def handoff_path(slug):
    return config.path("handoff", f"{slug}.md")


def read_handoff(slug):
    try:
        return open(handoff_path(slug), encoding="utf-8").read().strip()
    except OSError:
        return ""


def consume_handoff(slug, expected_digest=None):
    """Archive only after the new CLI confirms startup; never discard notes.

    An optional digest prevents a concurrently written handoff from being
    consumed by the session that received the previous version.
    """
    with _handoff_lock:
        p = handoff_path(slug)
        if not os.path.exists(p):
            return False
        if expected_digest is not None:
            with open(p, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != expected_digest:
                    return False
        ts = time.strftime("%Y%m%d-%H%M%S")
        target = config.path("handoff", "archive", f"{slug}-{ts}-{uuid.uuid4().hex[:10]}.md")
        os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
        os.replace(p, target)
        os.chmod(target, 0o600)
        return True


def write_handoff(slug, name, path, pane_tail="", archive_path=None):
    projects.invalidate(path)
    g = projects.git_info(path) if os.path.isdir(path) else None
    lines = [f"# Handoff for {slug}", f"Written {time.strftime('%Y-%m-%d %H:%M')} by blade {name}", ""]
    if g and g["is_git"]:
        lines.append(f"Branch: {g['branch']}  last commit: {g['last_commit']}")
        status = projects.sh(["git", "status", "--porcelain"], cwd=path)
        if status:
            lines.append(f"Uncommitted changes ({g['dirty']} files):")
            lines += ["    " + l for l in status.splitlines()[:40]]
        else:
            lines.append("Working tree clean.")
        if g["ahead"]:
            lines.append(f"Unpushed commits: {g['ahead']}")
        stash = projects.sh(["git", "stash", "list"], cwd=path)
        if stash:
            lines.append("Stashes:")
            lines += ["    " + l for l in stash.splitlines()[:10]]
        if not g["remote"]:
            lines.append("No GitHub remote configured.")
    else:
        lines.append("Not a git repository.")
    if archive_path:
        lines += ["", f"Full session archive: {archive_path}",
                  "The archive contains retained terminal scrollback, provider transcript when available, and recovery metadata."]
    tail = "\n".join(l.rstrip() for l in pane_tail.splitlines() if l.strip())[-6000:]
    if tail:
        lines += ["", "Last words of the closing session:", "```", tail, "```"]
    with _handoff_lock:
        # Preserve an unconsumed predecessor instead of silently overwriting it.
        consume_handoff(slug)
        target = handoff_path(slug)
        temp = target + "." + uuid.uuid4().hex + ".tmp"
        with open(temp, "x", encoding="utf-8") as f:
            os.chmod(temp, 0o600)
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, target)
    return handoff_path(slug)


def render(project):
    slug = project["slug"]
    cfg = config.project_config(slug)
    s = config.settings()
    template = cfg.get("prompt") or s["prompt_template"]
    all_by_slug = {p["slug"]: p for p in projects.parse_projects_md()}
    notes = f"\n\nProject notes:\n{cfg['notes'].strip()}\n" if cfg.get("notes", "").strip() else ""
    ho = read_handoff(slug)
    handoff = f"\n\nHandoff from the previous session (address these first):\n{ho}\n" if ho else ""
    g = projects.git_info(project["path"]) if project.get("exists") else None
    github = g["remote"]["url"] if g and g.get("remote") else "none"
    text = template.format(
        name=project["name"], path=project["path"], slug=slug,
        notes=notes, related=_related_block(cfg, all_by_slug),
        handoff=handoff, github=github)
    # collapse triple blank lines
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()
