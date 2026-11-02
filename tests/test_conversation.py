"""A card as a conversation (roadmap 25): a thread per card, next steps from
the app's own menu, and what the athlete tells the coach kept from their own
words - and only from them."""

from __future__ import annotations

import json
import sys
import time

import pytest

from runspector import paths, profile, tools
from runspector.web import agent, jobs, server

_REAL_VERIFY = agent.verify_facts


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNSPECTOR_HOME", str(tmp_path))
    monkeypatch.setattr(time, "sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def checked(monkeypatch):
    """The second check of noted facts (`agent.verify_facts`) without the CLI:
    by default it finds every fact in the message; each call is listed."""
    calls = []

    def fake(facts, words, earlier=""):
        calls.append((list(facts), words) if not earlier else (list(facts), words, earlier))
        return [True] * len(facts)
    monkeypatch.setattr(agent, "verify_facts", fake)
    return calls


def _run(monkeypatch, tmp_path, job, card: dict):
    """One card run whose model answers `card`."""
    def fake_run_once(job, db, athlete="", note=""):
        return 0, json.dumps({"result": json.dumps(card), "total_cost_usd": 0.01,
                              "num_turns": 4}), ""
    monkeypatch.setattr(agent, "_run_once", fake_run_once)
    return agent.run(job, db=str(tmp_path / "x.db"))


CARD = {"headline": "h", "bullets": ["[derived] b"], "verdict": "Rest today."}


def rem(fact, replaces=None):
    """One `remember` item as the model gives it."""
    return {"fact": fact, **({"replaces": replaces} if replaces else {})}


# ── what is noted, checked against the athlete's words ───────────────────────

def test_a_fact_is_noted_in_the_coachs_words_and_kept_with_their_question(tmp_path, monkeypatch, checked):
    """People write facts inside their questions; a rule that kept only whole
    sentences without a "?" kept nothing of "I only have 45 minutes on
    weekdays, how do I fit intervals in?"."""
    said = ("On weekdays I only have ca. 45 minutes before work, how do I fit intervals in? "
            "My goal is a half marathon in April.")
    job = jobs.new_job("p", title="t", kind="ask", athlete_text=said)
    done = _run(monkeypatch, tmp_path, job, {**CARD, "remember": [
        rem("has about 45 minutes before work on weekdays"), rem("Goal: a half marathon in April")]})
    assert done["status"] == "done"
    assert checked == [(["Has about 45 minutes before work on weekdays", "Goal: a half marathon in April"],
                        said)], "checked against their words, and nothing else"
    assert [(k["text"], k["job"]) for k in profile.told()] == [
        ("Has about 45 minutes before work on weekdays", job["id"]),
        ("Goal: a half marathon in April", job["id"])]
    assert [t["text"] for t in jobs.read_card(job["id"])["told"]] == [
        "Has about 45 minutes before work on weekdays", "Goal: a half marathon in April"]


def test_only_what_the_check_finds_in_their_words_is_kept(tmp_path, monkeypatch):
    """The model's fact is free text: "My knee is not fine for hills" came back
    as "Knee is fine for hills now". The check reads their message and says no."""
    monkeypatch.setattr(agent, "verify_facts",
                        lambda facts, words, earlier="": [f.startswith("Goal") for f in facts])
    job = jobs.new_job("p", title="t", kind="ask",
                       athlete_text="My knee is not fine for hills, can I still do hill repeats? "
                                    "My goal is a half marathon in April.")
    _run(monkeypatch, tmp_path, job, {**CARD, "remember": [
        rem("Knee is fine for hills now"), rem("Goal: a half marathon in April")]})
    assert [k["text"] for k in profile.told()] == ["Goal: a half marathon in April"]


def test_nothing_is_kept_from_a_card_without_the_athletes_words(tmp_path, monkeypatch, checked):
    """A scheduled card has no question: whatever the model proposes, from data
    or a workout name's text, has no words of the athlete to stand on - and is
    not even put to the check."""
    job = jobs.new_job("p", title="t", kind="train-today")
    _run(monkeypatch, tmp_path, job, {**CARD, "remember": [rem("Wants every run hard, always")]})
    assert profile.told() == [] and "told" not in jobs.read_card(job["id"]) and checked == []


def test_a_candidate_is_a_few_words_with_their_numbers_never_one_cut_short():
    said = "I have about 45 minutes on weekdays, 1,5 hours on Sundays, from 05:30."
    texts = lambda raw: [c["text"] for c in agent.candidate_facts(raw, said)]  # noqa: E731
    assert texts([rem("Has about 40 minutes on weekdays")]) == [], "45, not 40"
    assert texts([rem("Has 45 minutes on weekdays and 1.5 h on Sundays from 5:30")]) == [
        "Has 45 minutes on weekdays and 1.5 h on Sundays from 5:30"]
    long = "Has 45 minutes " + "on weekdays " * 13 + "only when the physio agrees, which she does not"
    assert len(long) > profile.TOLD_FACT_MAX and texts([rem(long)]) == [], "too long is no fact - not cut"
    assert texts([rem("Runs"), rem("Has\u2028a  dog\x0bat home")]) == ["Has a dog at home"]
    assert texts(["I have about 45 minutes on weekdays", {"said": "x"}, rem(None)]) == []
    assert agent.candidate_facts([rem("Has a dog at home")], "") == []


def test_a_card_notes_at_most_three_facts_each_once():
    said = "I run before work. I like trails. I have a treadmill. I own a heart strap."
    raw = [rem("Runs before work"), rem("runs before work."), rem("Likes trails a lot"),
           rem("Has a treadmill at home"), rem("Owns a heart strap")]
    assert [c["text"] for c in agent.candidate_facts(raw, said)] == ["Runs before work", "Likes trails a lot"]
    raw = [rem("Runs before work"), rem("Likes trails a lot"), rem("Has a treadmill at home"),
           rem("Owns a heart strap")]
    assert len(agent.candidate_facts(raw, said)) == agent.TOLD_PER_CARD == 3


# ── the check itself: a small model, no tools, their words only ──────────────

@pytest.fixture
def cli(tmp_path, monkeypatch):
    """A stand-in `claude`: writes its argv and stdin next to it and prints
    the envelope in `answer.txt` (or exits with `exit.txt`)."""
    d = tmp_path / "cli"
    d.mkdir()
    stub = d / "claude_stub.py"
    stub.write_text(
        "import json, pathlib, sys\n"
        f"d = pathlib.Path({str(d)!r})\n"
        "(d / 'argv.json').write_text(json.dumps(sys.argv[1:]))\n"
        "(d / 'stdin.txt').write_text(sys.stdin.read(), encoding='utf-8')\n"
        "a = sys.argv[1:]\n"
        "if '--mcp-config' in a:\n"
        "    (d / 'mcp.json').write_text(pathlib.Path(a[a.index('--mcp-config') + 1]).read_text())\n"
        "import time; time.sleep(float((d / 'slow.txt').read_text()) if (d / 'slow.txt').exists() else 0)\n"
        "code = int((d / 'exit.txt').read_text()) if (d / 'exit.txt').exists() else 0\n"
        "print((d / 'answer.txt').read_text() if (d / 'answer.txt').exists() else '')\n"
        "sys.exit(code)\n", encoding="utf-8")
    monkeypatch.setenv("RUNSPECTOR_CLAUDE_CMD", json.dumps([sys.executable, str(stub)]))
    monkeypatch.delenv("RUNSPECTOR_VERIFY_MODEL", raising=False)

    def answer(result, *, exit_code=0, is_error=False):
        (d / "answer.txt").write_text(json.dumps({"type": "result", "result": result, "is_error": is_error}))
        if exit_code:
            (d / "exit.txt").write_text(str(exit_code))
    answer.dir = d
    return answer


def test_the_check_reads_their_words_with_no_tool_and_no_server(cli):
    cli('{"supported": [true, false]}')
    said = "On weekdays I only have 45 minutes. Should I sign up for a marathon in April?"
    assert _REAL_VERIFY(["Has 45 minutes on weekdays", "Has a marathon in April"], said) == [True, False]
    argv = json.loads((cli.dir / "argv.json").read_text())
    assert argv[argv.index("--tools") + 1] == "" and "--strict-mcp-config" in argv
    assert argv[argv.index("--model") + 1] == "haiku" and "--print" in argv
    stdin = (cli.dir / "stdin.txt").read_text(encoding="utf-8")
    assert said in stdin and "1. Has 45 minutes on weekdays\n2. Has a marathon in April" in stdin
    assert "whenever you are not sure" in stdin


@pytest.mark.parametrize("result,kw", [
    ('{"supported": [true]}', {}),                       # one verdict for two facts
    ('{"supported": [true, "yes"]}', {}),                 # not a yes or no
    ("Both are supported.", {}),                          # no JSON
    ('{"supported": [true, true]}', {"exit_code": 1}),   # the CLI failed (the quota, a sign-in)
    ('{"supported": [true, true]}', {"is_error": True}),
])
def test_anything_but_a_clear_answer_keeps_nothing(cli, result, kw):
    cli(result, **kw)
    facts = ["Has 45 minutes on weekdays", "Likes trails a lot"]
    assert _REAL_VERIFY(facts, "I have 45 minutes. I like trails.") == [False, False]


def test_no_fact_needs_no_check(cli):
    assert _REAL_VERIFY([], "I have 45 minutes on weekdays") == []
    assert not (cli.dir / "argv.json").exists()


# ── what is kept, and how it changes ─────────────────────────────────────────

def test_a_changed_fact_takes_the_old_ones_place_and_the_newest_word_counts():
    """45, then 30, then 45 again: the third was dropped as a copy of the first
    and the coach went by the 30 - the athlete's newest word lost."""
    (a,) = profile.keep_told([{"text": "Has 45 minutes on weekdays"}], job="j1", day="2026-11-01")
    (b,) = profile.keep_told([{"text": "Has 30 minutes on weekdays", "replaces": a["id"]}],
                             job="j2", day="2026-11-05")
    assert [e["text"] for e in profile.told()] == ["Has 30 minutes on weekdays"]
    assert b["instead"] == "Has 45 minutes on weekdays"
    assert b["id"] == a["id"], "a fact keeps its id when it changes: one Forget reaches every copy"
    (c,) = profile.keep_told([{"text": "Has 45 minutes on weekdays"}], job="j3", day="2026-11-09")
    assert [(e["text"], e["day"]) for e in profile.told()] == [
        ("Has 30 minutes on weekdays", "2026-11-05"), ("Has 45 minutes on weekdays", "2026-11-09")]
    profile.keep_told([{"text": "has 45 minutes on weekdays!"}], job="j4", day="2026-11-10")
    newest = profile.told()[-1]
    assert (newest["text"], newest["day"]) == ("has 45 minutes on weekdays!", "2026-11-10")
    assert len(profile.told()) == 2, "the same fact said again moves, it is not doubled"
    before = [e["text"] for e in profile.told()]
    profile.keep_told([{"text": "Likes trails a lot", "replaces": "deadbeef"}], job="j5", day="d")
    assert [e["text"] for e in profile.told()] == [*before, "Likes trails a lot"], \
        "an id that is not there replaces nothing"


def test_kept_words_are_not_doubled_and_the_newest_stay(tmp_path):
    for i in range(profile.TOLD_MAX + 3):
        profile.keep_told([{"text": f"fact {i}"}], job="j", day="2026-11-01")
    texts = [e["text"] for e in profile.told()]
    assert len(texts) == profile.TOLD_MAX and "fact 0" not in texts
    assert texts[-1] == f"fact {profile.TOLD_MAX + 2}"


def test_the_you_tab_shows_the_question_from_its_card_and_it_goes_with_the_card(app, store):
    """The whole question was copied into the profile and outlived its card -
    "my divorce is stressing me" stayed on the You tab after the card was
    deleted, under a text that said it goes with the card."""
    app.store = store
    root = _card(question="I run with a club on Thursdays;\u2028my divorce is stressing me. Plan?")
    profile.keep_told([{"text": "Runs with a club on Thursdays"}], job=root, day="2026-11-02")
    assert "said" not in profile.told()[0] and "divorce" not in paths.profile_path().read_text()
    (t,) = app.week()["told"]
    assert t["said"] == "I run with a club on Thursdays; my divorce is stressing me. Plan?" and "job" not in t
    jobs.delete_card(root)
    (t,) = app.week()["told"]
    assert "said" not in t and t["text"] == "Runs with a club on Thursdays"


def test_every_kept_fact_the_you_tab_lists_reaches_the_coach():
    """30 facts were listed and the newest 8 read: an old injury dropped out of
    every card while the You tab still listed it as kept."""
    for i in range(profile.TOLD_MAX):
        profile.keep_told([{"text": f"{i:02d} " + "x" * (profile.TOLD_FACT_MAX - 3)}],
                          job="j", day="2026-11-01")
    text = profile.told_text(agent.TOLD_CAP)
    assert all(f"{i:02d} " in text for i in range(profile.TOLD_MAX))
    assert profile.told_text() == text, "a chat reads as much as a card"


def test_a_fact_is_let_go_and_the_settings_route_cannot_write_one():
    (e,) = profile.keep_told([{"text": "Runs in the morning"}], job="j", day="d")
    with pytest.raises(ValueError):
        profile.save({profile.TOLD_KEY: [{"id": "00000000", "text": "forged"}]})
    assert profile.forget_told(e["id"])["text"] == "Runs in the morning" and profile.told() == []
    assert profile.forget_told(e["id"]) is None and profile.forget_told("../x") is None


def test_the_coach_reads_the_kept_facts_in_cards_and_in_a_chat(store):
    (e,) = profile.keep_told([{"text": "Left knee does not like long downhills"}], job="j", day="2026-11-02")
    prompt = agent.build_prompt({"id": "j-x", "template_id": "plan-week", "prompt": "Plan."})
    assert "WHAT THE ATHLETE TOLD THE COACH" in prompt and "noted from their questions" in prompt
    assert f"- [{e['id']}] Left knee does not like long downhills (said 2026-11-02)" in prompt
    assert "knee does not like long downhills" in tools.get_athlete_profile(store)
    assert "never stand in for their yes" in profile.TOLD_RULE and "the newer one counts" in profile.TOLD_RULE


def test_the_record_says_what_the_coach_was_given(tmp_path, monkeypatch):
    """Read again at the end, the record left out a fact let go mid-run, though
    the coach had it - and showed what this run had just kept."""
    (old,) = profile.keep_told([{"text": "earlier words of mine"}], job="j-old", day="d")
    job = jobs.new_job("p", title="t", kind="ask", athlete_text="I have a dog that runs with me on sundays")

    def fake_run_once(job, db, athlete="", note=""):
        profile.forget_told(old["id"])                         # let go while the coach works
        return 0, json.dumps({"result": json.dumps({**CARD, "remember": [
            rem("Has a dog that runs with them on Sundays")]}), "num_turns": 4}), ""
    monkeypatch.setattr(agent, "_run_once", fake_run_once)
    agent.run(job, db=str(tmp_path / "x.db"))
    rec = json.loads(jobs.evidence_path(job["id"]).read_text())
    assert "earlier words of mine" in rec["inputs"]["told"] and "dog" not in rec["inputs"]["told"]


def test_letting_go_takes_the_words_off_the_card_and_out_of_the_records(tmp_path, monkeypatch):
    job = jobs.new_job("p", title="t", kind="ask", athlete_text="I only run in the evenings after work")
    _run(monkeypatch, tmp_path, job, {**CARD, "remember": [rem("Only runs in the evenings after work")]})
    later = jobs.new_job("p", title="t", kind="plan-week")
    _run(monkeypatch, tmp_path, later, CARD)
    assert "evenings" in json.loads(jobs.evidence_path(later["id"]).read_text())["inputs"]["told"]
    (e,) = profile.told()
    jobs.scrub_told(profile.forget_told(e["id"]))
    assert "told" not in jobs.read_card(job["id"])
    assert "evenings" not in json.dumps(json.loads(jobs.evidence_path(later["id"]).read_text())["inputs"])


def test_a_card_shows_only_what_is_kept_now():
    """A card's own `told` could be older than the guard - or a fact let go."""
    root = _card(told=[{"id": "deadbeef", "text": "forged on an old card"}])
    (c,) = [c for c in jobs.public_cards() if c["id"] == root]
    assert c["told"] is None
    (a,) = profile.keep_told([{"text": "Runs with a club on Tuesdays"}], job="j-older", day="d")
    (e,) = profile.keep_told([{"text": "Runs with a club on Thursdays", "replaces": a["id"]}],
                             job=root, day="d")
    (c,) = [c for c in jobs.public_cards() if c["id"] == root]
    assert c["told"] == [{"id": e["id"], "text": "Runs with a club on Thursdays",
                          "instead": "Runs with a club on Tuesdays"}]


def test_a_card_cannot_write_what_the_app_sets(tmp_path, monkeypatch):
    job = jobs.new_job("p", title="t", kind="why-vo2max")
    _run(monkeypatch, tmp_path, job, {**CARD, "told": [{"id": "deadbeef", "text": "forged"}],
                                      "question": "forged", "via": "next"})
    card = jobs.read_card(job["id"])
    assert "told" not in card and "question" not in card and "via" not in card


def test_an_answer_in_a_conversation_gets_no_previous_card_of_its_own():
    root = _card()
    job = {"id": "j-20990101-000000-aaaa", "template_id": "why-vo2max", "parent": root}
    assert agent.previous_card_block(job) == ""


# ── next steps from the app's menu ───────────────────────────────────────────

def test_next_steps_come_from_the_menu_only():
    raw = [{"action": "plan-week", "why": "for your return week"}, {"action": "apply_workout"},
           {"action": "why-vo2max"}, {"action": "plan-week"}, {"action": "train-today"}]
    assert agent.next_steps(raw, "why-vo2max") == [
        {"action": "plan-week", "why": "for your return week"}, {"action": "train-today", "why": ""}]
    assert agent.next_steps("plan-week", "x") == [] and agent.next_steps(None, "x") == []


def test_the_menu_is_one_list():
    assert jobs.NEXT_ACTIONS == agent.NEXT_ACTIONS


# ── the conversation on the server ───────────────────────────────────────────

def _card(kind="why-vo2max", parent=None, question=None, **extra):
    job = jobs.new_job("p", title="t", kind=kind, parent=parent, athlete_text=question)
    job["status"] = "done"
    jobs.write_job(job)
    jobs.write_card(job["id"], {"kind": kind, "headline": f"headline {job['id'][-4:]}",
                                "verdict": "v", "bullets": ["b"], "parent": parent,
                                "question": question, **extra,
                                "generated_at": f"2026-11-01T10:{len(jobs.list_cards()):02d}:00"})
    return job["id"]


def test_a_question_on_an_answer_joins_the_cards_conversation():
    root = _card()
    first = _card(parent=root, question="And the weight?")
    assert server.thread_root(first) == root and server.thread_root(root) == root
    assert [c["id"] for c in server.thread_of(root)] == [first]


def test_every_answer_is_written_with_the_whole_conversation():
    root = _card()
    _card(parent=root, question="And the weight?")
    prompt = server.follow_up_prompt({"id": root}, jobs.read_card(root), "And now?",
                                     server.thread_of(root))
    assert "the athlete asked: And the weight?" in prompt
    assert prompt.index("NOW (the athlete's own words): And now?") < prompt.index("--- CONVERSATION")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "claude_available", lambda: True)
    a = server.App.__new__(server.App)
    a.store = None
    a.templates = lambda: json.loads((server.STATIC.parent.parent / "templates.json").read_text())
    return a


def test_a_follow_up_on_an_answer_hangs_on_the_card(app):
    root = _card()
    ans = _card(parent=root, question="And the weight?")
    out, code = app.spawn({"from_job": ans, "prompt": "So what now?"})
    assert code == 200 and out["job"]["parent"] == root
    assert jobs.read_job(out["job"]["id"])["athlete_text"] == "So what now?"


def test_a_long_conversation_says_to_start_a_new_question(app):
    root = _card()
    for i in range(server.THREAD_MAX):
        _card(parent=root, question=f"q{i}")
    out, code = app.spawn({"from_job": root, "prompt": "one more"})
    assert code == 409 and out["code"] == "thread_full"


def test_a_next_step_starts_only_what_the_card_offers(app):
    root = _card(next=[{"action": "plan-week", "why": "return week"}])
    bad, code = app.spawn({"template_id": "week-review", "from_card": root})
    assert code == 400
    out, code = app.spawn({"template_id": "plan-week", "from_card": root})
    job = jobs.read_job(out["job"]["id"])
    assert code == 200 and job["parent"] == root and job["via"] == "next"
    assert "EARLIER CONVERSATION" in job["prompt"] and "headline" in job["prompt"]


def test_should_i_train_today_from_a_card_gets_today(app):
    root = _card(next=[{"action": "train-today"}])
    out, code = app.spawn({"template_id": "train-today", "from_card": root})
    assert code == 200 and jobs.read_job(out["job"]["id"])["ctx"] == {"day": server.paths.today().isoformat()}


def test_the_card_list_carries_the_next_steps_checked():
    root = _card(next=[{"action": "plan-week", "why": "x" * 200}, {"action": "apply_workout"}])
    (c,) = [c for c in jobs.public_cards() if c["id"] == root]
    assert c["next"] == [{"action": "plan-week", "why": "x" * 79 + "…"}]


def test_the_newest_answer_is_never_what_the_cap_cuts():
    """Oldest first under an 8,000-character cap: from the 4th question the
    newest answer and its question were cut, and the END marker with them."""
    big = {"bullets": ["[derived] " + "y" * 600] * 3, "verdict": "v" * 500}
    root = _card(**big)
    for i in range(5):
        _card(parent=root, question=f"question number {i}", **big)
    thread = server.thread_of(root)
    prompt = server.follow_up_prompt({"id": root}, jobs.read_card(root), "and now?", thread)
    full = agent.build_prompt({"id": "j-x", "template_id": "why-vo2max", "prompt": prompt}, told="")
    assert "the athlete asked: question number 4" in full and "--- END CONVERSATION" in full
    assert "earlier answer" in prompt, "the oldest are said to be left out, not silently cut"


def test_a_long_conversation_stays_whole_in_the_card_list():
    """Ten cards cut a long conversation off its card: its answers stood loose,
    without a field to ask on."""
    root = _card()
    for i in range(11):
        _card(parent=root, question=f"q{i}")
    ids = [c["id"] for c in jobs.public_cards()]
    assert root in ids and len(ids) == 12


def test_cleaning_up_drops_a_conversation_whole(monkeypatch):
    monkeypatch.setattr(jobs, "CARDS_MAX", 3)
    root = _card()
    answers = [_card(parent=root, question=f"q{i}") for i in range(2)]
    newer = [_card(kind="week-review") for _ in range(4)]
    jobs.cleanup_cards()
    left = {cid for cid, _ in jobs.list_cards()}
    assert set(newer[1:]) <= left and newer[0] not in left, "past the cap, not the newest of its kind"
    assert root in left and set(answers) <= left, "the conversation's kind is kept, and all of it"


def test_a_next_step_respects_the_conversation_limit_and_a_running_one(app):
    root = _card(next=[{"action": "plan-week"}])
    out, code = app.spawn({"template_id": "plan-week", "from_card": root})
    assert code == 200
    again, code = app.spawn({"template_id": "plan-week", "from_card": root})
    assert code == 409 and again["code"] == "already_running"
    for i in range(server.THREAD_MAX):
        _card(parent=root, question=f"q{i}")
    full, code = app.spawn({"template_id": "plan-week", "from_card": root})
    assert code == 409 and full["code"] == "thread_full"


# ── round 2 of the review ────────────────────────────────────────────────────

def test_a_question_on_an_answer_whose_card_is_gone_starts_from_that_answer(app):
    root = _card()
    ans = _card(parent=root, question="And the weight?")
    jobs.delete_card(root)
    assert server.thread_root(ans) == ans
    out, code = app.spawn({"from_job": ans, "prompt": "So what now?"})
    job = jobs.read_job(out["job"]["id"])
    assert code == 200 and job["parent"] == ans and "card no longer available" not in job["prompt"]


def test_a_next_step_is_not_started_twice_from_two_cards(app):
    a = _card(next=[{"action": "plan-week"}])
    b = _card(kind="week-review", next=[{"action": "plan-week"}])
    assert app.spawn({"template_id": "plan-week", "from_card": a})[1] == 200
    out, code = app.spawn({"template_id": "plan-week", "from_card": b})
    assert code == 409 and out["code"] == "already_running"


def test_the_conversation_limit_counts_the_answers_on_their_way(app):
    root = _card()
    for i in range(server.THREAD_MAX - 1):
        _card(parent=root, question=f"q{i}")
    assert app.spawn({"from_job": root, "prompt": "one"})[1] == 200          # 12th, running
    out, code = app.spawn({"from_job": root, "prompt": "two"})
    assert code == 409 and out["code"] == "thread_full"


def test_the_record_names_a_previous_card_only_when_the_coach_got_one(tmp_path, monkeypatch):
    root = _card()
    job = jobs.new_job("p", title="t", kind="why-vo2max", parent=root, athlete_text="and the weight question")
    _run(monkeypatch, tmp_path, job, CARD)
    assert json.loads(jobs.evidence_path(job["id"]).read_text())["inputs"]["previous_card"] is None


def test_letting_go_of_one_fact_leaves_a_longer_one_in_the_records(tmp_path, monkeypatch):
    profile.keep_told([{"text": "I run in the mornings"}], job="j1", day="d")
    profile.keep_told([{"text": "I run in the mornings before work with my dog"}], job="j2", day="d")
    later = jobs.new_job("p", title="t", kind="plan-week")
    _run(monkeypatch, tmp_path, later, CARD)
    gone = profile.forget_told(profile.told()[0]["id"])
    jobs.scrub_told(gone)
    told = json.loads(jobs.evidence_path(later["id"]).read_text())["inputs"]["told"]
    assert "before work with my dog" in told and profile.told_line(gone) not in told


def test_a_run_waiting_for_quota_takes_the_kept_words_afresh_on_each_try(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "QUOTA_SLICE_S", 5)
    (e,) = profile.keep_told([{"text": "words I let go while it waited"}], job="j", day="d")
    prompts = []

    def fake_run_once(job, db, athlete="", note=""):
        prompts.append(agent.build_prompt(job))
        if len(prompts) == 1:
            profile.forget_told(e["id"])
            return 1, "", "Claude usage limit reached; resets at 4pm"
        return 0, json.dumps({"result": json.dumps(CARD), "num_turns": 4}), ""
    monkeypatch.setattr(agent, "_run_once", fake_run_once)
    agent.run(jobs.new_job("p", title="t", kind="plan-week"), db=str(tmp_path / "x.db"))
    assert "let go while it waited" in prompts[0] and "let go while it waited" not in prompts[1]


def test_every_answer_in_a_conversation_carries_its_day():
    """An undated "my calf hurts" from Monday, in a "Should I train today?"
    started from the card on Thursday, read as today's symptom."""
    root = _card()
    jobs.write_card(_card(parent=root, question="My calf hurts a bit after yesterday"),
                    {**jobs.read_card(root), "parent": root,
                     "question": "My calf hurts a bit after yesterday",
                     "day": "2026-10-26", "generated_at": "2026-10-26T09:00:00"})
    prompt = server.step_context(jobs.read_card(root), server.thread_of(root))
    assert "On 2026-10-26 the athlete asked: My calf hurts" in prompt
    assert "not today's state" in prompt


# ── the last fresh reader ────────────────────────────────────────────────────

def test_asking_on_after_a_next_step_answers_as_that_steps_kind(app):
    """Under a VO2max card, a week was planned as its next step; "move the long
    run in that plan" was answered as a VO2max card that never saw the week."""
    root = _card()
    _card(kind="plan-week", parent=root, via="next", proposal="p-20261102-090000-abcd",
          headline="Return week: three easy runs")
    out, code = app.spawn({"from_job": root, "prompt": "Move the long run in that plan to Saturday"})
    job = jobs.read_job(out["job"]["id"])
    assert code == 200 and job["template_id"] == "plan-week"
    assert "Filed proposal: p-20261102-090000-abcd" in job["prompt"]


def test_letting_go_reaches_every_stored_answer_that_held_the_words(tmp_path):
    from runspector import evidence
    (e,) = profile.keep_told([{"text": "I only run in the evenings after work"}], job="j", day="2026-11-02")
    line = profile.told_line(e)
    jobs.write_card("j-20261102-100000-aaaa", {"kind": "plan-week", "headline": "h", "verdict": "v",
                                               "bullets": ["b"]})
    jobs.write_evidence("j-20261102-100000-aaaa", {"inputs": {"told": line},
                        "calls": [{"tool": "get_athlete_profile", "result": f"Profile\n{line}\nmore"}]})
    chat = evidence.chats_dir() / "2026-11-02-1.jsonl"
    chat.write_text(json.dumps({"tool": "get_athlete_profile", "result": f"x\n{line}\ny"}) + "\n")
    jobs.scrub_told(profile.forget_told(e["id"]))
    rec = jobs.read_evidence("j-20261102-100000-aaaa")
    assert "evenings" not in json.dumps(rec) and "more" in rec["calls"][0]["result"]
    assert json.loads(chat.read_text())["result"] == "x\ny", "the rest of the answer stays"


# ── a symptom said in a question: one tap from the card ──────────────────────

def test_a_card_offers_the_check_in_the_athlete_named_and_nothing_else(tmp_path, monkeypatch):
    """"My calf twinges" in a question was rest in that conversation and unknown
    in the next: the coach said "tap the check-in on the Today tab" and a new
    conversation filed a run for the next morning. The card now offers the
    button itself - the athlete's tap says it, never the coach."""
    job = jobs.new_job("p", title="t", kind="ask", athlete_text="My left calf twinges since yesterday. Run?")
    _run(monkeypatch, tmp_path, job, {**CARD, "check_in": "pain"})
    assert jobs.read_card(job["id"])["check_in"] == "pain"
    (c,) = [c for c in jobs.public_cards() if c["id"] == job["id"]]
    assert c["check_in"] == "pain"
    for bad in ("fever", "PAIN", ["pain"], None):
        other = jobs.new_job("p", title="t", kind="ask", athlete_text="My calf hurts. Run?")
        _run(monkeypatch, tmp_path, other, {**CARD, "check_in": bad})
        assert "check_in" not in jobs.read_card(other["id"]), bad
    data_only = jobs.new_job("p", title="t", kind="train-today")
    _run(monkeypatch, tmp_path, data_only, {**CARD, "check_in": "ill"})
    assert "check_in" not in jobs.read_card(data_only["id"]), "nobody said it"


def test_a_follow_up_is_cleaned_like_an_ask(app):
    """A line separator in a follow-up reached the kept fact's message and split
    its line in two, so a Forget missed every stored copy."""
    root = _card()
    out, code = app.spawn({"from_job": root, "prompt": "I run in the evenings.\u2028I have a dog. Plan?"})
    assert code == 200
    assert jobs.read_job(out["job"]["id"])["athlete_text"] == "I run in the evenings. I have a dog. Plan?"


def test_a_follow_up_is_checked_with_their_earlier_words_in_the_conversation(tmp_path, monkeypatch, checked):
    """"From next week only 30 minutes" was refused: alone, it did not say what
    the minutes are for - the "40 minutes before work" it changes stood two
    answers up. The check reads their earlier questions, never anything else."""
    root = _card(question="On weekdays I have about 40 minutes before work. How do I start?")
    _card(parent=root, question="I also have a treadmill.")
    job = jobs.new_job("p", title="t", kind="ask", parent=root, athlete_text="From next week only 30 now.")
    _run(monkeypatch, tmp_path, job, {**CARD, "remember": [rem("Only 30 minutes before work, next week")]})
    (_facts, words, earlier) = checked[-1]
    assert words == "From next week only 30 now."
    assert earlier == ("On weekdays I have about 40 minutes before work. How do I start?\n"
                       "I also have a treadmill.")
    assert agent.earlier_words({"id": "j-x"}) == "", "a card on its own has none"


def test_the_check_gets_the_earlier_words_marked_as_context(cli):
    cli('{"supported": [true]}')
    _REAL_VERIFY(["Only 30 minutes before work"], "Only 30 now.", "I have 40 minutes before work.")
    stdin = (cli.dir / "stdin.txt").read_text(encoding="utf-8")
    assert stdin.index("I have 40 minutes before work.") < stdin.index("Only 30 now.")
    assert "--- EARLIER" in stdin and "a note must be said in the MESSAGE" in stdin


# ── round 2 of the review of the checked memory ──────────────────────────────

def test_a_fact_replaces_a_kept_one_only_if_the_check_finds_that_change(tmp_path, monkeypatch, checked):
    """`replaces` was taken on trust: a card could drop any kept fact - the knee
    for a treadmill - since the check only saw the new fact."""
    (knee,) = profile.keep_told([{"text": "Left knee does not like long downhills"}], job="j0", day="d")
    job = jobs.new_job("p", title="t", kind="ask", athlete_text="I have a treadmill, intervals on it?")
    monkeypatch.setattr(agent, "verify_facts",
                        lambda facts, words, earlier="": [checked.append(facts) or "changes the kept" not in f
                                                          for f in facts])
    _run(monkeypatch, tmp_path, job, {**CARD, "remember": [rem("Has a treadmill at home", knee["id"])]})
    assert checked[-1] == ['Has a treadmill at home (changes the kept note: "Left knee does not like long '
                           'downhills")'], "the check sees the kept note it would drop"
    assert [e["text"] for e in profile.told()] == ["Left knee does not like long downhills"]


def test_a_replacement_the_check_finds_takes_the_place_and_the_id():
    (old,) = profile.keep_told([{"text": "Has 45 minutes on weekdays"}], job="j0", day="d")
    kept = agent.kept_facts([rem("Has 30 minutes on weekdays", old["id"]), rem("Owns a heart strap", "nope")],
                            "Only 30 minutes on weekdays now. I own a heart strap.")
    assert kept == [{"text": "Has 30 minutes on weekdays", "replaces": old["id"]},
                    {"text": "Owns a heart strap"}], "an id that is not kept is dropped before the check"


def test_one_forget_reaches_every_line_of_a_fact_said_again_or_changed(tmp_path):
    (a,) = profile.keep_told([{"text": "Has 45 minutes on weekdays"}], job="j1", day="2026-11-01")
    first = profile.told_line(a)
    (b,) = profile.keep_told([{"text": "Has 30 minutes on weekdays", "replaces": a["id"]}],
                             job="j2", day="2026-11-03")
    second = profile.told_line(b)
    assert first != second and b["id"] == a["id"]
    jobs.write_card("j-20261103-100000-aaaa", {"kind": "plan-week", "headline": "h", "verdict": "v",
                                               "bullets": ["b"]})
    jobs.write_evidence("j-20261103-100000-aaaa",
                        {"inputs": {"told": f"{first}\n- [cafebabe] Likes trails (said d)\n{second}"}})
    jobs.scrub_told(profile.forget_told(b["id"]))
    told = jobs.read_evidence("j-20261103-100000-aaaa")["inputs"]["told"]
    assert told == "- [cafebabe] Likes trails (said d)", "both days' lines gone, the other fact stays"


def test_a_check_in_is_offered_only_on_the_day_the_card_was_written():
    """The button went by the card's `day` - the run's date on a run's card,
    the week's Monday on a week's: today's pain hid, Monday showed Saturday's."""
    from runspector import paths as paths_mod
    today = paths_mod.today().isoformat()
    now = _card(kind="analyze-run", check_in="pain", day="2026-08-20")
    c = jobs.read_card(now)
    c["generated_at"] = f"{today}T09:00:00+01:00"
    jobs.write_card(now, c)
    old = _card(kind="plan-week", check_in="pain", day=today)
    c = jobs.read_card(old)
    c["generated_at"] = "2026-01-05T09:00:00+01:00"
    jobs.write_card(old, c)
    listed = {c["id"]: c["check_in"] for c in jobs.public_cards()}
    assert listed[now] == "pain" and listed[old] is None


def test_each_round_is_dated_by_when_it_was_asked():
    root = _card(kind="analyze-run", day="2026-08-20")
    reply = jobs.read_card(_card(kind="analyze-run", parent=root, question="My calf hurts since this run.",
                                 day="2026-08-20"))
    reply["generated_at"] = "2026-11-02T08:00:00+01:00"
    assert "On 2026-11-02 the athlete asked: My calf hurts" in "\n".join(
        server._reply_lines(reply, full=True))


def test_the_check_runs_with_an_empty_server_list_and_is_killed_at_its_deadline(cli, monkeypatch):
    cli('{"supported": [true]}')
    (cli.dir / "slow.txt").write_text("0")
    _REAL_VERIFY(["Has 45 minutes on weekdays"], "I have 45 minutes on weekdays")
    assert json.loads((cli.dir / "mcp.json").read_text()) == {"mcpServers": {}}, "not even the athlete's own"
    (cli.dir / "slow.txt").write_text("60")
    monkeypatch.setattr(agent, "VERIFY_TIMEOUT_S", 1)
    t0 = time.monotonic()
    assert _REAL_VERIFY(["Has 45 minutes on weekdays"], "I have 45 minutes on weekdays") == [False]
    assert time.monotonic() - t0 < 20 and agent._current[0] is None
