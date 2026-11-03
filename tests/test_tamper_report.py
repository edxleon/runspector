"""scripts/tamper_report.py: what a change does to the tests themselves, read from
two commits of a throw-away repository."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "tamper_report.py"

CI_YML = """jobs:
  test:
    steps:
      - name: tests
        env:
          COVERAGE: --cov=runspector --cov-fail-under=92
        run: pytest $COVERAGE
"""
BASE_FILES = {
    "tests/test_a.py": "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n",
    "tests/test_c.py": "def test_twin():\n    pass\n",
    "tests/test_d.py": "def test_twin():\n    pass\n",
    "web-tests/x.test.mjs": 'test("js one", () => {});\ntest("js two", () => {});\n',
    ".github/workflows/ci.yml": CI_YML,
    "pyproject.toml": "[project]\nname = 'x'\n",
    "tests/golden/week.txt": "Mon easy 40\n",
    "evals/cases.yaml": "cases:\n  - id: symptom\n    safety: true\n  - id: plain\n",
}
# The decorator line on its own: a word character right before the "@" reads as
# an e-mail address to scripts/pii_gate.py.
SKIPPED_ONE = "\n".join(["import pytest", "", "", "@pytest.mark.skip", "def test_one():", "    pass", ""])


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # Run from a git hook (the pre-push hook runs this suite), the environment
    # carries GIT_DIR and friends, which would point every call at this repo.
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key)
    spec = importlib.util.spec_from_file_location("tamper_report", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = tmp_path

    def commit(files: dict[str, str | bytes | None]) -> str:
        for path, text in files.items():
            p = tmp_path / path
            if text is None:
                p.unlink()
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        for args in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                     "commit", "-q", "-m", "x"]):
            subprocess.run(["git", *args], cwd=tmp_path, check=True)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True,
                              check=True).stdout.strip()

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    base = commit(BASE_FILES)
    return mod, base, commit


def test_a_clean_change_reports_nothing(repo):
    mod, base, commit = repo
    commit({"tests/test_a.py": BASE_FILES["tests/test_a.py"] + "\n\ndef test_three():\n    pass\n"})
    assert mod.report(base) == ([], [])


def test_every_way_a_change_can_weaken_the_tests_is_listed(repo):
    mod, base, commit = repo
    commit({
        # test_one moved to another file: moved, not gone. test_two deleted.
        "tests/test_a.py": None,
        "tests/test_b.py": SKIPPED_ONE,
        "tests/test_d.py": None,                                   # one of two tests named test_twin
        "web-tests/x.test.mjs": 'test("js one", { skip: true }, () => {});\n',
        ".github/workflows/ci.yml": CI_YML.replace("=92", "=90"),
        "tests/golden/week.txt": "Mon easy 45\n",
        "evals/cases.yaml": "cases:\n  - id: symptom\n  - id: plain\n    known: the judge reads it wrong\n",
    })
    lines, fail = mod.report(base)
    text = "\n".join(lines)
    gone = text.split("Markers")[0]
    assert "`test_two`" in gone and "`test_twin`" in gone and "`test_one`" not in gone, text
    assert "js: js two" in text and "skip: true" in text
    assert "@pytest.mark.skip" in text
    assert "92.0 -> 90.0" in text
    assert "tests/golden/week.txt" in text
    assert "newly accepted as failing (`known`):** `plain`" in text
    assert "safety case lost:** `symptom`" in text
    assert "This check itself changed" in text, "ci.yml is part of the check"
    assert any("coverage floor went down" in f for f in fail)
    assert any("safety case(s) lost" in f and "symptom" in f for f in fail)


def test_only_a_lower_floor_or_a_lost_safety_case_is_red(repo):
    mod, base, commit = repo
    commit({"tests/test_a.py": "def test_one():\n    pass\n",                    # a test deleted
            ".github/workflows/ci.yml": CI_YML.replace("=92", "=93")})          # the floor raised
    lines, fail = mod.report(base)
    assert lines and fail == [], (lines, fail)


def test_a_comment_is_not_the_floor(repo):
    """A floor read from the raw text took the first match, comments included:
    a comment above the real line both hid a lowered floor and invented one."""
    mod, base, commit = repo
    commit({".github/workflows/ci.yml": "# keep --cov-fail-under=92\n" + CI_YML.replace("=92", "=80")})
    lines, fail = mod.report(base)
    assert any("92.0 -> 80.0" in line for line in lines) and fail, (lines, fail)


@pytest.mark.parametrize(("ci", "pyproject", "red"), [
    (CI_YML.replace("--cov-fail-under=92", "--cov-fail-under 92"), None, False),       # pytest-cov takes both
    (CI_YML.replace(" --cov-fail-under=92", ""),
     "[project]\nname = 'x'\n\n[tool.coverage.report]\nfail_under = 92\n", False),     # moved, kept
    (CI_YML.replace(" --cov-fail-under=92", ""), None, True),                          # gone
])
def test_the_floor_is_found_where_it_may_live_and_missing_is_red(repo, ci, pyproject, red):
    mod, base, commit = repo
    commit({".github/workflows/ci.yml": ci, **({"pyproject.toml": pyproject} if pyproject else {})})
    _lines, fail = mod.report(base)
    assert bool(fail) is red, fail


def test_markers_and_options_that_leave_tests_out_are_listed(repo):
    mod, base, commit = repo
    commit({"tests/test_a.py": BASE_FILES["tests/test_a.py"] + '\nyaml = pytest.importorskip("yaml")\n'
                               "\n\ndef test_x():  # pragma: no branch\n    pass\n",
            "tests/conftest.py": 'collect_ignore = ["test_slow.py"]\n',
            "pyproject.toml": "[project]\nname = 'x'\n\n[tool.coverage.run]\nomit = ['src/big.py']\n"})
    text = "\n".join(mod.report(base)[0])
    for seen in ("importorskip", "pragma: no branch", "collect_ignore", "omit"):
        assert seen in text, (seen, text)


def test_a_file_that_is_not_utf8_is_read_not_a_crash(repo):
    """A Latin-1 fixture in the diff raised UnicodeDecodeError - red on a
    required check for a file that weakens nothing."""
    mod, base, commit = repo
    commit({"tests/fixtures/export.csv": "L\u00e4ufe;Zeit\n".encode("latin-1")})
    assert mod.report(base)[1] == []


def test_the_exit_code_says_red_and_a_missing_base_is_no_red(repo, capsys):
    mod, base, commit = repo
    assert mod.main(["--base", "0" * 40]) == 0, "a new branch: `before` is all zeros"
    assert mod.main(["--base", "not-a-commit"]) == 0
    commit({"evals/cases.yaml": "cases:\n  - id: plain\n"})
    assert mod.main(["--base", base]) == 1
    assert "**Red:**" in capsys.readouterr().out
