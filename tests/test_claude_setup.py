"""The Claude Code setup in `.claude/`: its deny rules hold in every permission mode,
so the ones that guard the public repository and the athlete's data are pinned here,
and its two hooks must work on a machine without `gh` or a network and never fail a
session."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE = ROOT / ".claude"


def test_the_deny_rules_guard_the_repository_and_the_athletes_data():
    deny = set(json.loads((CLAUDE / "settings.json").read_text(encoding="utf-8"))["permissions"]["deny"])
    for rule in ("Bash(git push --force*)", "Bash(git push * --force*)", "Bash(git push --no-verify*)",
                 "Bash(git push * --no-verify*)", "Bash(gh release delete*)", "Bash(git tag -d *)",
                 "Edit(~/.runspector/**)"):
        assert rule in deny, rule


def test_every_hook_runs_a_script_that_exists():
    hooks = json.loads((CLAUDE / "settings.json").read_text(encoding="utf-8"))["hooks"]
    commands = [h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]]
    assert commands
    for cmd in commands:
        script = cmd.split("$CLAUDE_PROJECT_DIR/")[1].split('"')[0]
        assert (ROOT / script).is_file(), script


def test_the_session_status_works_without_gh_and_never_fails():
    """No `gh`, no installed runspector on PATH: the git line still comes, the rest is
    skipped, and the hook exits 0."""
    git = shutil.which("git")
    env = {k: v for k, v in os.environ.items() if k not in ("PATH", "GH_TOKEN", "GITHUB_TOKEN")}
    env["PATH"] = str(Path(git).parent) if git else ""
    out = subprocess.run([sys.executable, str(CLAUDE / "hooks" / "session_status.py")], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "Traceback" not in out.stderr
    if git:
        assert out.stdout.startswith("[runspector] branch "), out.stdout


def _lint(path: str, project: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLAUDE / "hooks" / "lint_python.py")],
                          input=json.dumps({"tool_input": {"file_path": path}}),
                          env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)},
                          capture_output=True, text=True, timeout=60)


def test_the_lint_hook_hands_a_broken_file_back_and_lets_the_rest_pass(tmp_path):
    broken, clean = tmp_path / "broken.py", tmp_path / "clean.py"
    broken.write_text("x = (\n", encoding="utf-8")
    clean.write_text("x = 1\n", encoding="utf-8")
    out = _lint(str(broken), tmp_path)
    assert out.returncode == 2 and "broken.py" in out.stderr, (out.returncode, out.stderr)
    assert _lint(str(clean), tmp_path).returncode == 0
    assert _lint(str(tmp_path / "notes.md"), tmp_path).returncode == 0, "not Python"
    assert _lint(str(broken), tmp_path / "elsewhere").returncode == 0, "outside the project"
