"""scripts/cli_canary.py: the weekly check that the newest Claude Code still runs
a card as this app needs - its verdict, on init events of the shapes measured."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cli_canary.py"
spec = importlib.util.spec_from_file_location("cli_canary", SCRIPT)
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)

PLAIN = {"subtype": "init", "tools": ["t"] * 23, "memory_paths": {"auto": "/x/memory/"},
         "permissionMode": "default", "skills": ["a"]}
BARE = {"subtype": "init", "tools": ["Bash", "Read", "Edit"]}
CARD = {"subtype": "init", "tools": ["mcp__runspector__get_trend"], "permissionMode": "default", "skills": []}


def test_today_nothing_is_wrong():
    assert canary.problems(PLAIN, BARE, CARD, "") == []


def test_the_day_print_runs_bare_by_default_is_red():
    found = canary.problems(BARE, BARE, CARD, "")
    assert len(found) == 1 and "every card would fail" in found[0]


def test_any_option_the_card_run_passes_and_the_cli_dropped_is_named():
    """Not only the optional ones: a renamed --disallowedTools fails every card
    and was invisible to a check that only knew the optional flags."""
    found = canary.problems(PLAIN, BARE, None, "error: unknown option '--disallowedTools'")
    assert "the CLI no longer knows --disallowedTools: every card fails on it" in found
    assert any("did not start" in f for f in found)
    assert canary.problems(PLAIN, BARE, CARD, "error: unknown option '--effort'\n(Did you mean --x?)") == \
        ["the CLI no longer knows --effort: cards run without it (agent._OPTIONAL)"]


def test_settings_a_cli_silently_ignores_show_in_the_card_runs_init_event():
    """One invalid key voids the whole --settings JSON without a word on stderr:
    the init event is the only place it shows."""
    leaked = {**CARD, "memory_paths": {"auto": "/m/"}, "permissionMode": "auto", "skills": ["x"]}
    found = canary.problems(PLAIN, BARE, leaked, "")
    assert len(found) == 1 and "isolation did not hold" in found[0]
    assert "auto-memory" in found[0] and "'auto'" in found[0] and "1 skills" in found[0]


def test_no_init_event_at_all_is_red_not_a_pass():
    assert canary.problems(None, BARE, CARD, "")


def test_the_written_commands_are_the_apps_own_card_command(tmp_path):
    canary.write(tmp_path)
    script = (tmp_path / "run.sh").read_text(encoding="utf-8")
    card = next(line for line in script.splitlines() if "card.jsonl" in line)
    for flag in ("--strict-mcp-config", "--disallowedTools", "--allowedTools", "--settings", "--effort",
                 "/canary/mcp.json"):
        assert flag in card, flag
    assert str(tmp_path) not in script, "host paths are rewritten to where the folder is mounted"


def _outputs(folder: Path, card_err: str = "") -> None:
    import json
    folder.mkdir(parents=True, exist_ok=True)
    for name, ev in (("plain", PLAIN), ("bare", BARE), ("card", CARD)):
        (folder / f"{name}.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8")
    (folder / "version.txt").write_text("2.1.317 (Claude Code)\n", encoding="utf-8")
    (folder / "card.err").write_text(card_err, encoding="utf-8")


@pytest.mark.parametrize("nofollow", [True, False], ids=["posix", "no-O_NOFOLLOW-like-windows"])
def test_the_judge_follows_no_link_the_container_left(tmp_path, capsys, monkeypatch, nofollow):
    """The judge runs on the runner, the files come from the newest npm package
    in the container: a link there would print a runner file into the public log."""
    if not nofollow:
        monkeypatch.delattr(canary.os, "O_NOFOLLOW", raising=False)
    secret = tmp_path / "runner-only.txt"
    secret.write_text("RUNNER-SECRET\n", encoding="utf-8")
    out = tmp_path / "canary"
    _outputs(out)
    for name in ("version.txt", "card.err", "plain.jsonl"):
        (out / name).unlink()
        (out / name).symlink_to(secret)
    assert canary.judge(out) == 1
    printed = capsys.readouterr().out
    assert "RUNNER-SECRET" not in printed and "not followed" in printed, printed


def test_text_from_the_container_cannot_start_a_workflow_command(tmp_path, capsys):
    out = tmp_path / "canary"
    _outputs(out, card_err="error: unknown option '--effort'\n::add-mask::x\n::warning::planted")
    (out / "version.txt").write_text("2.1.317\n::notice::planted\n", encoding="utf-8")
    (out / "card.jsonl").write_text("", encoding="utf-8")       # did not start: its stderr is printed
    canary.judge(out)
    lines = capsys.readouterr().out.splitlines()
    assert not [ln for ln in lines if ln.startswith(("::add-mask", "::warning", "::notice"))], lines


def test_the_planted_runs_are_the_cards_of_both_sign_ins(tmp_path):
    """Control: the card without its isolation. Plan: no settings file read.
    Other: the settings read, the overrides alone - with the planted config
    folder's rules excluded where it is."""
    canary.write(tmp_path)
    script = (tmp_path / "run.sh").read_text(encoding="utf-8")
    run = {name: next(ln for ln in script.splitlines() if f"/{name} " in ln) for name in canary.RUNS}
    assert "--settings" not in run["control"] and "--setting-sources" not in run["control"]
    assert "--setting-sources ''" in run["plan"] and "--settings" in run["plan"]
    assert "--setting-sources" not in run["other"] and "/canary/planted/cfg/rules/**" in run["other"]
    assert all(script.count(f"/{name} ") == 2 for name in canary.RUNS), "each through both projects"
    assert (tmp_path / "planted" / "cfg" / "CLAUDE.md").read_text(encoding="utf-8").startswith("MARKER_")


def _sent(folder: Path, run: str, text: str) -> None:
    (folder / "cap" / run).mkdir(parents=True, exist_ok=True)
    (folder / "cap" / run / "000.json").write_text(text, encoding="utf-8")


def test_only_planted_markers_are_read_from_what_a_run_sent(tmp_path):
    _sent(tmp_path, "plan", '{"model":"claude-sonnet-5-5-canary","output_config":{"effort":"low"},'
                            '"system": "MARKER_USER_RULES MARKER_NOT_OURS Always respond in german."}')
    assert canary.recorded(tmp_path, "plan") == {"MARKER_USER_RULES", "MARKER_LANGUAGE",
                                                 "MARKER_MODEL_MAPPING", "MARKER_MAX_EFFORT"}
    assert canary.recorded(tmp_path, "other") is None, "nothing sent is not 'nothing leaked'"
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "cap" / "other").symlink_to(tmp_path / "elsewhere")
    assert canary.recorded(tmp_path, "other") is None and "cap/other" in canary.unlinked(tmp_path)


def test_a_leak_in_either_sign_in_is_red_and_a_stale_plant_is_a_note():
    everything = set(canary.PLANTED)
    clean = {"control": everything, "plan": set(), "other": set()}
    assert canary.planted_problems(clean, {}) == ([], [])
    reds, _ = canary.planted_problems({**clean, "plan": {"MARKER_USER_RULES"}}, {})
    assert len(reds) == 1 and "--setting-sources" in reds[0] and "MARKER_USER_RULES" in reds[0]
    reds, _ = canary.planted_problems({**clean, "other": {"MARKER_AGENT"}}, {})
    assert len(reds) == 1 and "agent.isolation()" in reds[0]
    # Only unread settings keep a model mapping and an effort cap out: expected
    # where they are read, red where they are not.
    assert canary.planted_problems({**clean, "other": set(canary.ONLY_UNREAD)}, {}) == ([], [])
    reds, _ = canary.planted_problems({**clean, "plan": {"MARKER_MAX_EFFORT"}}, {})
    assert reds and "--setting-sources" in reds[0]
    reds, notes = canary.planted_problems({**clean, "control": everything - {"MARKER_HOOK"}}, {})
    assert reds == [] and "MARKER_HOOK" in notes[0]
    reds, _ = canary.planted_problems({**clean, "control": set()}, {})
    assert reds and "planted check did not run" in reds[0], "a plant that reaches nothing checks nothing"
    bypass = {"plan": {**CARD, "permissionMode": "bypass"}}
    reds, _ = canary.planted_problems({**clean, "other": None}, bypass)
    assert any("other card run sent nothing" in r for r in reds)
    assert any("plan card run's init event" in r and "'bypass'" in r for r in reds)


def test_an_option_value_the_cli_ignores_and_what_the_init_event_may_not_show_are_red():
    found = canary.problems(PLAIN, BARE, CARD, "Warning: Unknown --effort value 'medium' - ignoring it")
    assert found == ["the CLI no longer takes the value runspector passes to --effort: cards run on the "
                     "athlete's own setting"]
    shown = {**CARD, "tools": ["Bash", "mcp__runspector__get_trend"],
             "plugins": [{"name": "cc-plugin-agents-md", "source": "cc-plugin-agents-md@builtin"}]}
    leak = canary.problems(PLAIN, BARE, shown, "")
    assert len(leak) == 1 and "built-in tools (Bash)" in leak[0] and "AGENTS.md plugin" in leak[0]
