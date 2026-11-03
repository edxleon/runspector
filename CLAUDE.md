@AGENTS.md

The rules live in `AGENTS.md`, which other coding agents read too; this file only imports them.

What `.claude/` adds for Claude Code:
- a status at session start (`hooks/session_status.py`): uncommitted and unpushed work, open pull
  requests, the last run of every workflow - a red weekly run comes first;
- ruff on every Python file right after it is edited (`hooks/lint_python.py`);
- deny rules that hold in every permission mode: no force push, no `--no-verify`, no deleting a
  release, tag, branch or the repo, nothing written into `~/.runspector` (real athlete data);
- `/deep-review` (the review AGENTS.md asks for) and `/session-end`;
- two reviewers: `mcp-tool-reviewer` for a tool change, `coach-prompt-reviewer` for a prompt change -
  use them before the commit, and before an eval run is spent.
