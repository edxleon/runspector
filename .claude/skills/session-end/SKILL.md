---
name: session-end
description: Close a working session on runspector - state of the tree, branches and pull requests, the last workflow runs, and the project memory brought up to date so the next session starts where this one stopped. Use when the maintainer says "session end" or asks for a handoff.
disable-model-invocation: true
---

# Session end

The repository is public, so the session's story does not go into a file in it. It goes
into commits and pull requests (what changed and why) and into the project's auto memory
(what the next session needs and cannot read from the repository). This skill makes sure
nothing of the session is left only in the conversation.

## 1. Facts

Run, in parallel:

```bash
git status --short
git branch --no-merged origin/main          # local work that never reached main
git log --oneline origin/main..HEAD
gh pr list --state open
gh run list --limit 15
```

- Uncommitted or unpushed work of this session: commit it on a branch and open a pull
  request, or name it as an open thread below. Never leave it unmentioned.
- A red or cancelled run of `evals`, `sweep`, `mutation` or `scorecard`: read its summary
  now and say what it was.

## 2. The project memory

The memory index (`MEMORY.md`) loads at every session start; the status memory of the
project holds the state. Update it - or create it, if there is none - with:

- **Shipped:** version, date, what changed for the runner, in one line per release.
- **Open:** each item with owner (maintainer or session) and the date it was added;
  remove what this session finished.
- **Learned:** only what a future session would otherwise get wrong and cannot find in
  the code, AGENTS.md or the commits - a measurement, a pitfall, a decision with its
  reason. Not a diary.

A rule that can be checked mechanically belongs in a test or hook instead of the memory
(AGENTS.md lists the existing gates); say so as an open item if it is more than a few
lines.

## 3. AGENTS.md

If this session found a new "when you change X, also do Y", add the row.

## 4. Handoff

```
## Session end
Done:
- ...
Next session starts with: <the most concrete next step>
Open threads: <only when there are any>
```

Push, merge and release stay the maintainer's word.
