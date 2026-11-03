"""Every file and `module.symbol` the agent instructions name still exists.

AGENTS.md, CLAUDE.md, CONTRIBUTING.md, evals/README.md and everything under
`.claude/` are read by coding agents as instructions, and agents follow them -
a pointer to a renamed file or a deleted function sends the next session to
look for it, or to recreate it. A rename that forgets these files fails here.
"""

from __future__ import annotations

import importlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTRUCTIONS = [ROOT / "AGENTS.md", ROOT / "CLAUDE.md", ROOT / "CONTRIBUTING.md",
                ROOT / "evals" / "README.md", *sorted((ROOT / ".claude").rglob("*.md"))]
EXT = (".py", ".md", ".yaml", ".yml", ".json", ".jsonc", ".mjs", ".js", ".toml", ".sql", ".html", ".css",
       ".txt")
#: Named on purpose and not in the repository: the token folder inside the
#: athlete's data directory, Claude Code's memory index, and an eval run's
#: untracked output.
OUTSIDE = {"garmin/", "MEMORY.md", "last-run.json"}
MODULES = ("runspector.", "runspector.web.")


def _tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return {Path(p).name for p in out.stdout.splitlines()}


def unresolved(text: str, base: Path, tracked: set[str]) -> list[str]:
    """The pointers in `text` (backticked paths and `module.symbol`s) that point at nothing."""
    local = {p.stem for d in (ROOT / "src" / "runspector", ROOT / "src" / "runspector" / "web")
             for p in d.glob("*.py")}
    missing = []
    for span in re.findall(r"`([^`\n]+)`", text):
        for tok in span.split():
            tok = tok.strip("(),;:'\"")
            if not tok or tok.startswith(("~", "/", "$", "http", "-")) or tok in OUTSIDE \
                    or re.search(r"X\.Y\.Z|<|>|\{", tok):
                continue
            if "/" in tok or tok.endswith(EXT):
                path = re.sub(r":\d+.*$", "", tok)
                roots = (ROOT, ROOT / "src" / "runspector", ROOT / ".claude", base)
                if "/" not in path.rstrip("/"):      # a bare name: a file anywhere in the repo will do
                    if path not in tracked and not any((r / path).exists() for r in roots):
                        missing.append(tok)
                    continue
                if not any((r / path).exists() or ("*" in path and any(r.glob(path))) for r in roots):
                    missing.append(tok)
            elif re.fullmatch(r"[a-z_]+\.[A-Za-z_]\w*", tok) and tok.split(".")[0] in local:
                name, attr = tok.split(".")
                found = False
                for pkg in MODULES:
                    try:
                        found = hasattr(importlib.import_module(pkg + name), attr)
                    except ImportError:
                        continue
                    if found:
                        break
                if not found:
                    missing.append(tok)
    return missing


def test_every_pointer_in_the_agent_instructions_resolves():
    tracked = _tracked()
    stale = {str(f.relative_to(ROOT)): got for f in INSTRUCTIONS
             if (got := unresolved(f.read_text(encoding="utf-8"), f.parent, tracked))}
    assert not stale, f"pointers to nothing: {stale}"


def test_a_stale_pointer_is_caught():
    tracked = _tracked()
    text = ("see `src/runspector/plan.py` and `plan.retry_of`, run `uv run python evals/run_evals.py`; "
            "gone: `src/runspector/nope.py`, `plan.no_such_function`, `evals/missing.yaml`, `gone.yaml`")
    assert unresolved(text, ROOT, tracked) == ["src/runspector/nope.py", "plan.no_such_function",
                                               "evals/missing.yaml", "gone.yaml"]
