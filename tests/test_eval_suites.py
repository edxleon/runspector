"""The two eval suites of roadmap 25 are well formed, and measure the app's own
check and routes, never a copy (`evals/run_memory_check.py`,
`evals/run_conversations.py`). The runs need the real model; their SHAPE does not."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from runspector.web import agent

EVALS = Path(__file__).resolve().parent.parent / "evals"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, EVALS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_note_of_the_check_has_its_verdict():
    cases = yaml.safe_load((EVALS / "memory_check.yaml").read_text(encoding="utf-8"))["cases"]
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)) and len(cases) >= 30
    for c in cases:
        assert isinstance(c["message"], str) and c["message"].strip(), c["id"]
        assert 1 <= len(c["notes"]) <= agent.TOLD_PER_CARD, c["id"]
        for n in c["notes"]:
            assert n["keep"] in (True, False, None) and n["note"].strip(), c["id"]
    keeps = [n["keep"] for c in cases for n in c["notes"]]
    assert keeps.count(True) >= 10 and keeps.count(False) >= 10, "both ways, or a yes-man passes"
    assert keeps.count(None) <= 2, "\"either is right\" is the exception, each with its reason"


def test_the_check_suite_runs_the_apps_check_and_its_control_fits_the_same_frame():
    run = _module("run_memory_check")
    assert run.agent is agent, "the app's check, imported - never a copy"
    fields = {"nonce": "ab12cd34", "message": "m", "notes": "1. n", "earlier": ""}
    assert agent.VERIFY_PROMPT.format(**fields) and run.BARE.format(**fields)


def test_every_conversation_step_is_one_action_with_known_checks():
    spec = yaml.safe_load((EVALS / "conversations.yaml").read_text(encoding="utf-8"))
    actions = {"ask", "follow", "template", "forget"}
    checks = {"while_pending", "memory_has", "memory_lacks", "memory_max", "check_in", "prompt_has",
              "given_lacks", "proposal", "judge"}
    proposal_keys = {"adds", "takes_off", "only", "minutes", "kinds", "within_days"}
    ids = [s["id"] for s in spec["stories"]]
    assert len(ids) == len(set(ids))
    for story in spec["stories"]:
        assert "ask" in story["steps"][0], f"{story['id']}: a conversation starts with a question"
        for step in story["steps"]:
            assert len(actions & set(step)) == 1 and set(step) <= actions | checks, (story["id"], step)
            if "proposal" in step:
                assert set(step["proposal"]) <= proposal_keys, (story["id"], step["proposal"])
            if "judge" in step:
                assert "PASS" in step["judge"] and "FAIL" in step["judge"], \
                    f"{story['id']}: a judge question says what passes and what fails"
    assert _module("run_conversations").run_evals.JUDGE, "the coach evals' judge, shared"
