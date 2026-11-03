"""SessionStart hook: the state of the repository in a few lines, added to the
session's context (Claude Code adds a SessionStart hook's stdout to it).

What it answers before any work starts: is there work in the tree that is not
committed or not on GitHub, which pull requests are open, did the last run of
each workflow pass - above all the weekly ones (evals, sweep, mutation,
scorecard), which nobody watches - and is the installed `runspector` the
version in this tree.

Cheap and fail-soft: every line is skipped when its source is not there (no
`gh`, no network, no installed tool), the whole hook never fails a session.
Standard library only.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ("ci", "evals", "sweep", "mutation", "scorecard")
#: A weekly workflow whose last run is older than this has stopped running.
STALE_DAYS = 9


def run(*cmd: str, timeout: float = 8) -> str | None:
    try:
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                             encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def git_line() -> str | None:
    branch = (run("git", "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    if not branch:
        return None
    dirty = len([ln for ln in (run("git", "status", "--porcelain") or "").splitlines() if ln.strip()])
    ahead = (run("git", "rev-list", "--count", "origin/main..HEAD") or "").strip()
    line = f"branch {branch} · {dirty} uncommitted"
    if ahead.isdigit() and int(ahead):
        line += f" · {ahead} commit(s) not on origin/main"
    if branch == "main":
        line += " · main moves only through a pull request"
    return line


def age(stamp: str) -> str:
    try:
        then = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return "?"
    hours = (datetime.now(UTC) - then).total_seconds() / 3600
    return f"{hours:.0f}h" if hours < 48 else f"{hours / 24:.0f}d"


def runs_line() -> str | None:
    raw = run("gh", "run", "list", "--limit", "60", "--json",
              "workflowName,conclusion,status,headBranch,createdAt,event")
    if raw is None:
        return None
    latest: dict[str, dict] = {}
    for r in json.loads(raw):
        name = r.get("workflowName")
        if name in WORKFLOWS and name not in latest and r.get("headBranch") == "main" \
                and r.get("event") != "pull_request":
            latest[name] = r
    parts, attention = [], []
    for name in WORKFLOWS:
        r = latest.get(name)
        if r is None:
            continue
        if r.get("status") != "completed":
            parts.append(f"{name} running")
            continue
        ok = r.get("conclusion") == "success"
        when = age(r.get("createdAt", ""))
        parts.append(f"{name} {'ok' if ok else r.get('conclusion')} ({when} ago)")
        if not ok:
            attention.append(f"{name} {r.get('conclusion')}")
        elif name != "ci" and when.endswith("d") and int(when[:-1]) > STALE_DAYS:
            attention.append(f"{name} has not run for {when}")
    if not parts:
        return None
    line = "last runs on main: " + " · ".join(parts)
    if attention:
        line += "\n  ⚠ read before anything else: " + "; ".join(attention) + \
                " (`gh run list --workflow <name>.yml`, the run's summary)"
    return line


def prs_line() -> str | None:
    raw = run("gh", "pr", "list", "--state", "open", "--json", "number,title,author,isDraft")
    if raw is None:
        return None
    prs = json.loads(raw)
    if not prs:
        return "open pull requests: none"
    items = [f"#{p['number']} {p['title'][:60]}" for p in prs[:5]]
    more = f" (+{len(prs) - 5} more)" if len(prs) > 5 else ""
    return f"open pull requests ({len(prs)}): " + "; ".join(items) + more


def version_line() -> str | None:
    m = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    repo = m.group(1) if m else "?"
    exe = shutil.which("runspector")
    installed = (run(exe, "--version", timeout=10) or "").strip() if exe else ""
    line = f"version in this tree {repo}"
    if installed:
        line += f" · installed {installed}" + ("" if installed == repo else " (differs)")
    try:
        with socket.create_connection(("127.0.0.1", 8765), timeout=0.3):
            line += " · something serves :8765 (the dev server?)"
    except OSError:
        pass
    return line


def main() -> int:
    lines = []
    for build in (git_line, prs_line, runs_line, version_line):
        try:
            got = build()
        except Exception:  # noqa: BLE001 - a status line must never fail the session start
            got = None
        if got:
            lines.append(got)
    if lines:
        sys.stdout.write("[runspector] " + "\n[runspector] ".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
