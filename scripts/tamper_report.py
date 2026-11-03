"""What a change does to the tests themselves, as a report in the CI summary.

Most of this code is written by a model, and a model that cannot make a test
pass is known to edit the test instead - delete it, skip it, loosen the floor.
Each of those can be right, and then it should be visible in the review, not
discovered later. This lists, between a base commit and HEAD:

- test functions that disappeared (Python and the node tests, counted by name:
  a test moved to another file did not disappear, one of two equal names did),
- skip, xfail, importorskip and no-cover/no-branch markers that were added, and
  options in ci.yml, pyproject.toml or a conftest that leave tests or code out,
- the coverage floor (ci.yml's pytest options, else pyproject's fail_under),
- golden files that were rewritten,
- eval cases removed, newly accepted as `known`, or no longer `safety`,
- a change to this check itself (CI runs the base commit's copy of it).

It fails only on the two that never pass unnoticed: a lower or vanished
coverage floor and a safety case lost (removed, or `safety: true` taken away).
Everything else is a note for the reviewer.

    uv run python scripts/tamper_report.py --base <sha or ref>
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ".github/workflows/ci.yml"
SELF = ("scripts/tamper_report.py", CI)
EVAL_FILES = ("evals/cases.yaml", "evals/conversations.yaml", "evals/memory_check.yaml")
MARKER = re.compile(r"\bmark\.(skip|skipif|xfail)\b|pytest\.(skip|xfail|importorskip)\("
                    r"|pragma: no (cover|branch)|\bcollect_ignore"
                    r"|\b(test|it|describe)\.(skip|todo)\(|\bt\.(skip|todo)\("
                    r"|\b(skip|todo):\s*true")
LOOSEN = re.compile(r"--deselect|--ignore|\s-k\s|\bomit\b|exclude_also|exclude_lines|--cov-fail-under")
FLOOR = re.compile(r"--cov-fail-under[= ](\d+(?:\.\d+)?)")
JS_TEST = re.compile(r"""^\s*(?:test|it)\(\s*(["'`])(.+?)\1""", re.M)


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    return out.stdout if out.returncode == 0 else ""


def code(text: str) -> str:
    """Text for a Markdown code span: a backtick in a name must not end it."""
    return "`" + text.replace("`", "'") + "`"


def files_at(rev: str, prefix: str, suffix: str) -> list[str]:
    listed = git("ls-tree", "-r", "--name-only", rev, "--", prefix).splitlines()
    return [p for p in listed if p.endswith(suffix)]


def show(rev: str, path: str) -> str:
    return git("show", f"{rev}:{path}")


def tests_at(rev: str) -> tuple[Counter, dict[str, str]]:
    """(how often each test name occurs, a file it occurs in) at `rev`."""
    count: Counter = Counter()
    where: dict[str, str] = {}
    for path in files_at(rev, "tests", ".py"):
        try:
            tree = ast.parse(show(rev, path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test"):
                count[node.name] += 1
                where.setdefault(node.name, path)
    for path in files_at(rev, "web-tests", ".mjs"):
        for m in JS_TEST.finditer(show(rev, path)):
            name = f"js: {m.group(2)}"
            count[name] += 1
            where.setdefault(name, path)
    return count, where


def _strings(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def floor(rev: str) -> float | None:
    """The coverage floor at `rev`: pytest's --cov-fail-under in ci.yml (read as
    YAML, so a comment never counts), else fail_under in pyproject.toml."""
    try:
        doc = yaml.safe_load(show(rev, CI)) or {}
    except yaml.YAMLError:
        doc = {}
    for s in _strings(doc):
        if m := FLOOR.search(s):
            return float(m.group(1))
    try:
        cov = tomllib.loads(show(rev, "pyproject.toml") or "").get("tool", {}).get("coverage", {})
    except tomllib.TOMLDecodeError:
        return None
    value = (cov.get("report") or {}).get("fail_under")
    return float(value) if isinstance(value, int | float) else None


def cases(rev: str, path: str) -> dict[str, dict]:
    try:
        doc = yaml.safe_load(show(rev, path)) or {}
    except yaml.YAMLError:
        return {}
    items = doc.get("cases") or doc.get("stories") or []
    return {c["id"]: c for c in items if isinstance(c, dict) and c.get("id")}


def added_lines(base: str, *paths: str) -> list[tuple[str, str]]:
    """(file, line) for every line the change adds in `paths`."""
    out, path = [], ""
    for line in git("diff", "-U0", f"{base}...HEAD", "--", *paths).splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("+"):
            out.append((path, line[1:].strip()))
    return out


def report(base: str) -> tuple[list[str], list[str]]:
    """(lines of the report, reasons to fail)."""
    lines, fail = [], []
    changed = set(git("diff", "--name-only", f"{base}...HEAD").splitlines())

    touched_self = sorted(changed & set(SELF))
    if touched_self:
        lines.append("**This check itself changed** (" + ", ".join(code(p) for p in touched_self)
                     + ") - in CI the base commit's copy of the script runs; read the change.")

    (before, where), (after, _) = tests_at(base), tests_at("HEAD")
    gone = sorted(n for n in before if after[n] < before[n])
    if gone:
        lines.append(f"**Tests that disappeared ({len(gone)}):**")
        lines += [f"- {code(n)} (was in {code(where[n])})" for n in gone]

    markers = [(p, t) for p, t in added_lines(base, "tests", "web-tests", "src") if MARKER.search(t)]
    markers += [(p, t) for p, t in added_lines(base, CI, "pyproject.toml") if LOOSEN.search(t)]
    markers += [(p, t) for p, t in added_lines(base, "tests/conftest.py")
                if MARKER.search(t) or LOOSEN.search(t)]
    if markers:
        lines.append(f"**Markers or options that leave tests or code out ({len(markers)}):**")
        lines += [f"- {code(p)}: {code(t[:100])}" for p, t in dict.fromkeys(markers)]

    old, new = floor(base), floor("HEAD")
    if old != new:
        lines.append(f"**Coverage floor:** {old} -> {new}")
        if old is not None and (new is None or new < old):
            fail.append(f"the coverage floor went down ({old} -> {new})")

    golden = sorted(p for p in changed if p.startswith("tests/golden/"))
    if golden:
        lines.append("**Golden files rewritten:** " + ", ".join(code(p) for p in golden))

    for path in EVAL_FILES:
        b, h = cases(base, path), cases("HEAD", path)
        removed = sorted(set(b) - set(h))
        known = sorted(i for i in h if h[i].get("known") and not (b.get(i) or {}).get("known"))
        lost = sorted(i for i in b if b[i].get("safety") and not (h.get(i) or {}).get("safety"))
        if removed:
            lines.append(f"**{code(path)}, removed:** " + ", ".join(code(i) for i in removed))
        if known:
            lines.append(f"**{code(path)}, newly accepted as failing (`known`):** "
                         + ", ".join(code(i) for i in known))
        if lost:
            lines.append(f"**{code(path)}, safety case lost:** " + ", ".join(code(i) for i in lost))
            fail.append(f"safety case(s) lost in {path}: {', '.join(lost)}")
    return lines, fail


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True,
                    help="the commit to compare HEAD with (the diffs use its merge base with HEAD)")
    args = ap.parse_args(argv)
    is_commit = git("rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}").strip()
    if not args.base.strip("0") or not is_commit:
        print(f"base {args.base!r} is not a commit here - nothing to compare "
              "(a new branch, a shallow checkout?)")
        return 0
    lines, fail = report(args.base)
    out = ["## What this change does to the tests", ""]
    out += lines or ["Nothing: no test removed, skipped or loosened, no eval case dropped."]
    if fail:
        out += ["", "**Red:** " + "; ".join(fail)]
    text = "\n".join(out) + "\n"
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
