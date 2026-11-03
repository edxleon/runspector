"""PostToolUse hook (Edit|Write): ruff on the Python file just edited.

A lint or syntax error then shows up at the edit that made it, not a few minutes
later in the pre-push hook or twenty in CI. Exit 2 hands ruff's lines back to
Claude as feedback; exit 0 for anything else - not a Python file, a file outside
this repository, or no ruff (the hook runs in the project environment, where
the dev group has it).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    path = (event.get("tool_input") or {}).get("file_path") or ""
    if not path.endswith(".py"):
        return 0
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2]).resolve()
    file = Path(path).resolve()
    if root not in file.parents or not file.is_file():
        return 0
    try:
        out = subprocess.run([sys.executable, "-m", "ruff", "check", "--output-format", "concise",
                              "--no-cache", str(file)],
                             cwd=root, capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.SubprocessError):
        return 0
    if out.returncode == 0 or "No module named ruff" in out.stderr:
        return 0
    sys.stderr.write(f"ruff on {file.relative_to(root)}:\n{out.stdout.strip() or out.stderr.strip()}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
