"""What a change does to the tests themselves, as a report in the CI summary.

Most of this code is written by a model, and a model that cannot make a test
pass is known to edit the test instead - delete it, skip it, loosen the floor.
Each of those can be right, and then it should be visible in the review, not
discovered later. This lists, between a base commit and HEAD:

- test functions that disappeared (Python and the node tests, counted by name:
  a test moved to another file did not disappear, one of two equal names did),
- skip, xfail, importorskip and no-cover/no-branch markers that were added, and
  options in ci.yml, pyproject.toml or a conftest that leave tests or code out,
- the coverage floor where CI sets it (the COVERAGE value of ci.yml's test job,
  else pyproject's fail_under) - a floor no longer there is a note, not a red:
  it may have moved,
- golden files that were rewritten,
- eval cases removed, newly accepted as `known`, or no longer `safety`,
- a change to this check itself (CI runs the base commit's copy of it).

It fails only on the two that never pass unnoticed: a lower coverage floor and
a safety case lost (removed, or `safety: true` taken away). Everything else is a
note for the reviewer. It shows what an agent did to the tests; it is no
barrier against someone set on hiding it - a change to the workflow itself is
the deep review's (AGENTS.md).

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
SELF = "scripts/tamper_report.py"
#: The lines of ci.yml that make up this check and the floor it reads.
CI_SELF = re.compile(r"tamper_report|fetch-depth|cov-fail-under|COVERAGE")
#: Where pytest and coverage take options from, besides ci.yml.
CONFIGS = ("pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini", ".coveragerc", "conftest.py",
           "tests/conftest.py")
#: The tests of this script hold every marker as a string.
OWN_TESTS = "tests/test_tamper_report.py"
EVAL_FILES = ("evals/cases.yaml", "evals/conversations.yaml", "evals/memory_check.yaml")
MARKER = re.compile(r"\bmark\.(skip|skipif|xfail)\b|\b(skip|xfail|importorskip)\(|SkipTest"
                    r"|pragma:?\s*no\s*(cover|branch)|\bcollect_ignore|__test__\s*=\s*False"
                    r"|\b(test|it|describe)\.(skip|todo)\(|\bt\.(skip|todo)\(|\b(skip|todo):\s*\S",
                    re.I)
LOOSEN = re.compile(r"--deselect|--ignore|\s-k\s|-m\s|--no-cov|PYTEST_ADDOPTS|\baddopts\b|\btestpaths\b"
                    r"|\bomit\b|exclude_also|exclude_lines|fail_under|--cov-fail-under", re.I)
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
    # Only where CI sets it: the COVERAGE value of the test job's steps. Text
    # anywhere else (a step's name, a comment in a script) is not the floor.
    steps = (((doc.get("jobs") or {}).get("test") or {}).get("steps") or []) if isinstance(doc, dict) else []
    for step in steps if isinstance(steps, list) else []:
        cov = ((step or {}).get("env") or {}).get("COVERAGE") if isinstance(step, dict) else None
        for s in _strings(cov):
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
    return {str(c["id"]): c for c in items if isinstance(c, dict) and c.get("id") is not None}


def added_lines(base: str, *paths: str) -> list[tuple[str, str]]:
    """(file, line) for every line the change adds in `paths`."""
    out, path = [], ""
    # --text: a `-diff` in .gitattributes must not turn a test file into a binary
    # with no lines to read.
    for line in git("diff", "--text", "-U0", f"{base}...HEAD", "--", *paths).splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("+"):
            out.append((path, line[1:].strip()))
    return out


def removed_lines(base: str, *paths: str) -> list[tuple[str, str]]:
    """(file, line) for every line the change removes in `paths`."""
    out, path = [], ""
    for line in git("diff", "--text", "-U0", f"{base}...HEAD", "--", *paths).splitlines():
        if line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else line[4:]
        elif line.startswith("-"):
            out.append((path, line[1:].strip()))
    return out


def report(base: str) -> tuple[list[str], list[str]]:
    """(lines of the report, reasons to fail)."""
    lines, fail = [], []
    changed = set(git("diff", "--name-only", f"{base}...HEAD").splitlines())

    if SELF in changed:
        lines.append(f"**This check itself changed** ({code(SELF)}) - in CI the base commit's copy of "
                     "the script runs; read the change.")
    if any(CI_SELF.search(t) for _p, t in added_lines(base, CI) + removed_lines(base, CI)):
        lines.append(f"**The CI step of this check or the floor's line changed** ({code(CI)}) - read it.")
    if ".gitattributes" in changed:
        lines.append(f"**{code('.gitattributes')} changed** - it decides how git diffs the tests.")

    (before, where), (after, _) = tests_at(base), tests_at("HEAD")
    gone = sorted(n for n in before if after[n] < before[n])
    if gone:
        lines.append(f"**Tests that disappeared ({len(gone)}):**")
        lines += [f"- {code(n)} (was in {code(where[n])})" for n in gone]

    markers = [(p, t) for p, t in added_lines(base, "tests", "web-tests", "src")
               if MARKER.search(t) and p != OWN_TESTS]
    markers += [(p, t) for p, t in added_lines(base, CI, *CONFIGS) if LOOSEN.search(t) or MARKER.search(t)]
    if markers:
        lines.append(f"**Markers or options that leave tests or code out ({len(markers)}):**")
        lines += [f"- {code(p)}: {code(t[:100])}" for p, t in dict.fromkeys(markers)]

    old, new = floor(base), floor("HEAD")
    if old is not None and new is None:
        lines.append(f"**Coverage floor:** {old} -> not found where CI sets it "
                     "(moved, or removed?) - read it.")
    elif old != new:
        lines.append(f"**Coverage floor:** {old} -> {new}")
        if old is not None and new < old:
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
    # Unknown options pass: CI runs the BASE commit's copy of this script with
    # the arguments of the change, which may know a newer option.
    args, _unknown = ap.parse_known_args(argv)
    is_commit = git("rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}").strip()
    if not args.base.strip("0") or not is_commit:
        return _emit([f"base {args.base!r} is not a commit here - nothing to compare "
                      "(a new branch, a shallow checkout?)"], [])
    return _emit(*report(args.base))


def _emit(lines: list[str], fail: list[str]) -> int:
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
