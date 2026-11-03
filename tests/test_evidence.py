"""What the AI read, kept and checked (`evidence.py`), end to end: the card run's
stream, the number check, the marks on each point, the card's record on disk
and behind its route, and the chat record of the MCP server."""

from __future__ import annotations

import asyncio
import http.client
import json
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from runspector import evidence, mcp_server
from runspector.store import Store
from runspector.web import agent, jobs, server


def _event(kind: str, *blocks: dict) -> str:
    return json.dumps({"type": kind, "message": {"role": kind, "content": list(blocks)}})


def _stream(calls: list[tuple[str, dict, object]], card: dict, *, num_turns: int = 3) -> str:
    """A CLI session as `--output-format stream-json` prints it: init, each tool
    call and its result, the answer, the result event."""
    lines = [json.dumps({"type": "system", "subtype": "init", "model": "stub", "tools": []})]
    for i, (name, args, result) in enumerate(calls):
        lines.append(_event("assistant", {"type": "tool_use", "id": f"toolu_{i}",
                                          "name": f"mcp__runspector__{name}", "input": args}))
        lines.append(_event("user", {"type": "tool_result", "tool_use_id": f"toolu_{i}",
                                     "content": result}))
    answer = json.dumps(card)
    lines.append(_event("assistant", {"type": "text", "text": answer}))
    lines.append(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                             "result": answer, "num_turns": num_turns, "total_cost_usd": 0.02,
                             "modelUsage": {"stub": {"costUSD": 0.02}}}))
    return "\n".join(lines) + "\n"


# ── the stream ───────────────────────────────────────────────────────────────

def test_the_stream_gives_every_call_with_what_came_back_and_the_final_envelope():
    out = _stream([("get_training_readiness", {}, "HRV 52 ms, baseline 61"),
                   ("get_trend", {"metric": "sleep_score", "days": 28},
                    [{"type": "text", "text": "sleep 71"}, {"type": "text", "text": "mean 74"}])],
                  {"headline": "h", "bullets": [], "verdict": "v"})
    s = evidence.parse_stream(out)
    assert s["stream"] is True and s["complete"] is True
    assert s["envelope"]["num_turns"] == 3 and s["envelope"]["total_cost_usd"] == 0.02
    assert [c["tool"] for c in s["calls"]] == ["get_training_readiness", "get_trend"]
    assert s["calls"][0]["result"] == "HRV 52 ms, baseline 61"
    assert s["calls"][1]["input"] == {"metric": "sleep_score", "days": 28}
    assert s["calls"][1]["result"] == "sleep 71\nmean 74"     # a list of text blocks, joined


def test_the_one_object_answer_is_still_read_with_no_calls_known():
    """`--output-format json` prints the envelope alone; a CLI or a stub that
    still does must not cost the card - it only has no record of the reads."""
    s = evidence.parse_stream(json.dumps({"result": "x", "num_turns": 3}))
    assert s["envelope"] == {"result": "x", "num_turns": 3}
    assert s["calls"] == [] and s["stream"] is False


def test_a_cut_stream_says_it_is_incomplete_and_keeps_its_envelope():
    out = _stream([("get_training_readiness", {}, "HRV 52")], {"headline": "h"})
    cut = "[... 9000 bytes dropped ...]\n" + out[40:]           # the tail cap, mid-line
    s = evidence.parse_stream(cut)
    assert s["complete"] is False
    assert s["envelope"] is not None and s["envelope"]["num_turns"] == 3


def test_garbage_has_no_envelope():
    assert evidence.parse_stream("Error: something broke")["envelope"] is None
    assert evidence.parse_stream("")["envelope"] is None


# ── the numbers ──────────────────────────────────────────────────────────────

def test_numbers_are_values_not_labels():
    assert evidence.numbers("HRV 38 ms on 2026-11-01, 5x4 at 4:35/km, VO2max 48.3, Z5, 8 %") \
        == ["38", "2026-11-01", "5", "4:35", "48.3", "8"]


@pytest.mark.parametrize("card_says,data,found", [
    ("48", "VO2max 47.9", True),          # the card rounds to its own precision
    ("47.9", "VO2max 48", False),         # ...but more precision than the data has is new
    ("4:35", "pace 4:35/km", True),
    ("4:36", "pace 4:35/km", False),
    ("2026-11-01", "day 2026-11-01", True),
    ("52", "HRV 52 ms", True),
    ("53", "HRV 52 ms", False),
])
def test_a_number_is_found_only_as_the_data_holds_it(card_says, data, found):
    r = evidence.check({"headline": f"value {card_says}", "bullets": []}, data=data)
    assert (r["unmatched"] == []) is found


def test_the_check_says_where_a_number_was_not_found_and_where_others_were():
    card = {"headline": "HRV 52 and 13.7 below", "verdict": "easy",
            "bullets": ["sleep 71", "your note says 3 days", "nothing here"]}
    r = evidence.check(card, data="HRV 52 sleep 71", context="you run 3 days a week")
    assert r["numbers"] == 4 and r["in_data"] == 2
    assert r["unmatched"] == [{"where": "headline", "value": "13.7"}]
    assert [p["unmatched"] for p in r["points"]] == [[], [], []]


def test_a_point_marked_as_your_data_with_no_number_from_the_data_is_flagged():
    card = {"headline": "h", "bullets": ["sleep 93.4", "a rule of 48 hours"]}
    sources = [{"kind": "measured", "ref": "get_trend", "tool": "get_trend"},
               {"kind": "measured", "ref": None, "tool": None}]
    r = evidence.check(card, data="HRV 52", rules="hard stimuli sit 48 hours apart", sources=sources)
    assert r["points"][0] == {"unmatched": ["93.4"], "no_data": True}
    # 48 is in the rules, not in the data: not "found nowhere", but no data either
    assert r["points"][1] == {"unmatched": [], "no_data": True}


# ── the marks ────────────────────────────────────────────────────────────────

def test_the_mark_leaves_the_text_and_becomes_the_points_source():
    texts, sources = evidence.split_marks([
        "[measured: get_training_readiness] HRV 38 ms",
        "[Derived] easy because of it",
        "[you] Tuesdays are short",
        "[measured - today's readiness] no tool named",
        "[opinion] an unknown mark stays text",
        "no mark at all",
        42,
    ])
    assert texts == ["HRV 38 ms", "easy because of it", "Tuesdays are short", "no tool named",
                     "[opinion] an unknown mark stays text", "no mark at all", 42]
    assert sources[0] == {"kind": "measured", "ref": "get_training_readiness",
                          "tool": "get_training_readiness"}
    assert sources[1] == {"kind": "derived", "ref": None, "tool": None}
    assert sources[2]["kind"] == "you"
    assert sources[3] == {"kind": "measured", "ref": "today's readiness", "tool": None}
    assert sources[4] is None and sources[5] is None and sources[6] is None


def test_the_record_names_a_tool_the_coach_never_called():
    stream = evidence.parse_stream(_stream([("get_training_readiness", {}, "HRV 52")], {}))
    card = {"headline": "h", "verdict": "v",
            "bullets": ["HRV 52", "sleep 93.4"]}
    sources = [{"kind": "measured", "ref": "get_training_readiness", "tool": "get_training_readiness"},
               {"kind": "measured", "ref": "get_trend", "tool": "get_trend"}]
    doc, summary, checked = evidence.card_record(
        job_id="j-x", model="stub", stream=stream, card=card, sources=sources,
        inputs={"task": "t"}, data_rules="", data_context="")
    assert checked[0]["called"] is True and checked[0]["no_data"] is False
    assert checked[1]["called"] is False and checked[1]["unmatched"] == ["93.4"]
    assert summary == {"recorded": True, "complete": True, "checked": True, "reads": 1, "numbers": 2,
                       "in_data": 1, "missing": 1, "unmatched": [{"where": "point 2", "value": "93.4"}]}
    assert doc["calls"] == [{"tool": "get_training_readiness", "input": {}, "error": False,
                             "chars": 6, "result": "HRV 52"}]


def test_the_record_caps_a_runaway_result_but_checks_the_whole_of_it():
    big = "x " * evidence.RESULT_CAP + " HRV 52"
    stream = evidence.parse_stream(_stream([("get_trend", {}, big)], {}))
    doc, summary, _ = evidence.card_record(
        job_id="j-x", model="", stream=stream, card={"headline": "HRV 52", "bullets": []},
        sources=[], inputs={}, data_rules="", data_context="")
    assert summary["unmatched"] == []                       # found past the cap
    assert len(doc["calls"][0]["result"]) < len(big)
    assert "more characters" in doc["calls"][0]["result"]


# ── the card run keeps it ────────────────────────────────────────────────────

STREAM_STUB = '''\
import json, sys
from pathlib import Path
here = Path(__file__).parent
sys.stdin.read()
with open(here / "runs.txt", "a", encoding="utf-8") as fh:
    fh.write("run\\n")
print((here / "out.jsonl").read_text(encoding="utf-8"), end="")
rc = here / "rc.txt"
sys.exit(int(rc.read_text()) if rc.exists() else 0)
'''


@pytest.fixture()
def stream_cli(tmp_path, monkeypatch):
    d = tmp_path / "stub"
    d.mkdir()
    (d / "claude_stub.py").write_text(STREAM_STUB, encoding="utf-8")
    monkeypatch.setenv("RUNSPECTOR_CLAUDE_CMD", json.dumps([sys.executable, str(d / "claude_stub.py")]))

    def answer(out: str, rc: int = 0) -> Path:
        (d / "out.jsonl").write_text(out, encoding="utf-8")
        (d / "rc.txt").write_text(str(rc), encoding="utf-8")
        return d
    return answer


CARD = {"kind": "train-today", "ref": "", "headline": "Easy today: HRV 52 ms.", "verdict": "easy",
        "bullets": ["[measured: get_training_readiness] HRV 52 ms, 9 under your baseline of 61",
                    "[derived] Hard sessions wait until it is back over 58.6",
                    "[measured: get_trend] Sleep 93.4 all week"]}


def test_a_card_run_writes_its_record_before_the_card_and_the_card_its_sources(stream_cli, tmp_path):
    stream_cli(_stream([("get_training_readiness", {}, "HRV 52 ms (baseline 61, 9 under)")], CARD))
    job = jobs.new_job("Should I train today?", title="Today", kind="train-today")
    done = agent.run(job, db=str(tmp_path / "t.db"))
    assert done["status"] == "done", done["result_summary"]

    card = jobs.read_card(job["id"])
    assert card["bullets"] == ["HRV 52 ms, 9 under your baseline of 61",
                               "Hard sessions wait until it is back over 58.6",
                               "Sleep 93.4 all week"]          # the marks left the text
    assert [s["kind"] for s in card["sources"]] == ["measured", "derived", "measured"]
    assert card["sources"][0]["called"] is True and card["sources"][0]["unmatched"] == []
    assert card["sources"][2]["called"] is False and card["sources"][2]["no_data"] is True
    assert card["evidence"]["recorded"] is True and card["evidence"]["reads"] == 1
    assert {"where": "point 3", "value": "93.4"} in card["evidence"]["unmatched"]

    doc = jobs.read_evidence(job["id"])
    assert doc["calls"][0]["tool"] == "get_training_readiness"
    assert doc["calls"][0]["result"] == "HRV 52 ms (baseline 61, 9 under)"
    assert doc["inputs"]["task"] == "Should I train today?" and doc["inputs"]["rules_sha256"]

    pub = next(c for c in jobs.public_cards() if c["id"] == job["id"])
    assert pub["sources"][0]["from"] == "today's readiness"
    assert pub["evidence"]["reads"] == 1


def test_a_failing_record_never_costs_the_card(stream_cli, tmp_path, monkeypatch):
    stream_cli(_stream([("get_training_readiness", {}, "HRV 52")], CARD))

    def boom(**_kw):
        raise OSError("disk full")
    monkeypatch.setattr(evidence, "card_record", boom)
    job = jobs.new_job("x", title="t", kind="train-today")
    done = agent.run(job, db=str(tmp_path / "t.db"))
    assert done["status"] == "done"
    card = jobs.read_card(job["id"])
    assert card["bullets"][0] == "HRV 52 ms, 9 under your baseline of 61"   # still unmarked text
    assert "evidence" not in card and jobs.read_evidence(job["id"]) is None


def test_the_one_turn_guard_still_holds_on_the_stream(stream_cli, tmp_path):
    stream_cli(_stream([], CARD, num_turns=1))
    job = jobs.new_job("x", title="t", kind="train-today")
    done = agent.run(job, db=str(tmp_path / "t.db"))
    assert done["status"] == "failed" and "without reading any data" in done["result_summary"]
    assert jobs.read_card(job["id"]) is None


def test_the_record_goes_with_its_card(stream_cli, tmp_path, monkeypatch):
    stream_cli(_stream([("get_training_readiness", {}, "HRV 52")], CARD))
    job = jobs.new_job("x", title="t", kind="train-today")
    agent.run(job, db=str(tmp_path / "t.db"))
    assert jobs.evidence_path(job["id"]).is_file()
    assert jobs.delete_card(job["id"])
    assert not jobs.evidence_path(job["id"]).exists()

    # ...and with the cleanup's: an old card of a kind with a newer one
    monkeypatch.setattr(jobs, "CARDS_MAX", 0)
    old, new = (jobs.new_job("x", title="t", kind="train-today") for _ in range(2))
    for j, at in ((old, "2026-01-01T00:00:00+00:00"), (new, "2026-02-01T00:00:00+00:00")):
        jobs.write_card(j["id"], {"kind": "train-today", "headline": "h", "bullets": [],
                                  "verdict": "v", "generated_at": at})
        jobs.write_evidence(j["id"], {"version": 1})
    jobs.cleanup_cards()
    assert not jobs.evidence_path(old["id"]).exists()
    assert jobs.evidence_path(new["id"]).exists()


# ── the route ────────────────────────────────────────────────────────────────

@pytest.fixture()
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNSPECTOR_HOME", str(tmp_path))
    app = server.App.__new__(server.App)
    app.demo, app.token, app._token_fails = True, None, 0
    app.db_path = str(tmp_path / "t.db")
    app.store = Store(app.db_path)
    app.reset_runtime_state()
    httpd = server.make_server("127.0.0.1", 0, app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _get(httpd, path: str) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    finally:
        conn.close()


def test_the_route_gives_the_record_in_plain_words_and_says_when_there_is_none(live):
    job = jobs.new_job("x", title="t", kind="train-today")
    jobs.write_card(job["id"], {"kind": "train-today", "headline": "h", "bullets": [], "verdict": "v"})
    status, body = _get(live, f"/api/cards/{job['id']}/evidence")
    assert status == 200 and body["evidence"]["recorded"] is False     # made before the record

    jobs.write_evidence(job["id"], {
        "version": 1, "recorded": True, "complete": True, "model": "stub",
        "calls": [{"tool": "apply_workout", "input": {"proposal_id": "p-1"}, "error": False,
                   "result": "<script>x</script>"}],
        "inputs": {"task": "t", "about_you": "a", "secret": "not passed on"},
        "check": {"numbers": 2, "in_data": 1, "unmatched": [{"where": "point 1", "value": "9"}]}})
    status, body = _get(live, f"/api/cards/{job['id']}/evidence")
    ev = body["evidence"]
    assert status == 200 and ev["recorded"] is True
    assert ev["calls"][0]["label"] == "a session put on your watch" and ev["calls"][0]["changed"] is True
    assert ev["calls"][0]["result"] == "<script>x</script>"      # raw: the page escapes it
    assert "secret" not in ev["inputs"]
    assert ev["check"]["unmatched"] == [{"where": "point 1", "value": "9"}]

    assert _get(live, "/api/cards/j-20990101-000000-dead/evidence")[0] == 404
    assert _get(live, "/api/cards/..%2f..%2fetc/evidence")[0] == 404


def test_the_state_tells_the_page_what_is_kept_and_where(live):
    evidence.record_chat_call("get_trend", {"metric": "hrv_avg_ms", "days": 28}, ok=True, ms=12,
                              result="HRV 52")
    status, body = _get(live, "/api/state")
    rec = body["ai_record"]
    assert rec["chat_days"] == evidence.CHAT_DAYS and rec["cards_kept"] == jobs.CARDS_MAX
    assert rec["where"].endswith("evidence")
    assert rec["chats"][0]["label"] == "a trend over time"
    assert rec["chats"][0]["detail"] == "hrv avg ms, 28 days"


# ── the chat record ──────────────────────────────────────────────────────────

def test_every_tool_the_server_offers_has_words_for_the_page():
    registered = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert registered <= set(evidence.TOOL_LABELS), registered - set(evidence.TOOL_LABELS)
    assert registered >= evidence.CHANGING_TOOLS


def test_a_chat_tool_call_is_recorded_with_what_came_back(monkeypatch):
    monkeypatch.delenv("RUNSPECTOR_UNATTENDED", raising=False)
    asyncio.run(mcp_server.mcp.call_tool("get_coaching_reference", {}))
    lines = [json.loads(x) for p in evidence.chats_dir().glob("*.jsonl")
             for x in p.read_text(encoding="utf-8").splitlines()]
    assert [r["tool"] for r in lines] == ["get_coaching_reference"]
    assert lines[0]["ok"] is True and lines[0]["chars"] > 1000 and "ms" in lines[0]
    assert evidence.recent_chat_calls()[0]["label"] == "the coaching rules"


def test_a_failing_tool_call_is_recorded_as_failed_and_still_fails(monkeypatch):
    monkeypatch.delenv("RUNSPECTOR_UNATTENDED", raising=False)
    with pytest.raises(Exception):  # noqa: B017 — whatever FastMCP raises, it must still raise
        asyncio.run(mcp_server.mcp.call_tool("get_daily_metrics", {"day": "not a day"}))
    rows = evidence.recent_chat_calls()
    assert rows[0]["tool"] == "get_daily_metrics" and rows[0]["ok"] is False


def test_a_card_run_is_not_recorded_as_a_chat(monkeypatch):
    monkeypatch.setenv("RUNSPECTOR_UNATTENDED", "1")
    asyncio.run(mcp_server.mcp.call_tool("get_coaching_reference", {}))
    assert evidence.recent_chat_calls() == []


def test_a_record_that_cannot_be_written_never_costs_the_answer(monkeypatch):
    monkeypatch.delenv("RUNSPECTOR_UNATTENDED", raising=False)

    def broken():
        raise PermissionError("read-only disk")
    monkeypatch.setattr(evidence, "chats_dir", broken)
    out = asyncio.run(mcp_server.mcp.call_tool("get_coaching_reference", {}))
    assert out                                                # the answer came back


def test_the_chat_record_keeps_its_days_and_no_more():
    def day(n: int) -> str:
        return f"{datetime.now() - timedelta(days=n):%Y-%m-%d}"
    old = evidence.chats_dir() / f"{day(evidence.CHAT_DAYS)}-1.jsonl"      # today + 13 days back = 14
    kept = evidence.chats_dir() / f"{day(evidence.CHAT_DAYS - 1)}-1.jsonl"
    for p in (old, kept):
        p.write_text("{}\n", encoding="utf-8")
    evidence.prune_chats()
    assert not old.exists() and kept.exists()


# ── found in the live run on a copy of real data ────────────────────────────

def test_a_date_followed_by_a_colon_is_still_a_date():
    """The tools print history as "- 2026-10-16: 49.4"; the check read "2026"
    there and so missed every such date a card quoted, flagging a measured
    point as not backed by the data."""
    assert evidence.numbers("- 2026-10-16: 49.4") == ["2026-10-16", "49.4"]
    assert evidence.numbers("week 1: 30 min, at 4:35") == ["1", "30", "4:35"]
    r = evidence.check({"headline": "VO2max 49.4 on 2026-10-16", "bullets": []},
                       data="the days below:\n- 2026-10-16: 49.4\n- 2026-10-17: 49.3")
    assert r["unmatched"] == []


def test_a_tool_result_the_cli_hands_over_as_json_is_read_as_its_text():
    """The CLI passes an MCP tool's answer on as `{"result": "..."}` - the
    structured form. Kept as it came, "What came back" showed escaped JSON,
    and a number after an escaped line break ("\\n52") was glued to the "n"."""
    out = "\n".join([
        _event("assistant", {"type": "tool_use", "id": "t1", "name": "mcp__runspector__get_trend",
                             "input": {}}),
        _event("user", {"type": "tool_result", "tool_use_id": "t1",
                        "content": json.dumps({"result": "HRV\n52 ms\n- 2026-10-12: 57.2"})}),
        json.dumps({"type": "result", "result": "{}", "num_turns": 2}),
    ])
    call = evidence.parse_stream(out)["calls"][0]
    assert call["result"] == "HRV\n52 ms\n- 2026-10-12: 57.2"
    # anything else that merely looks like JSON stays as it came
    other = out.replace(json.dumps(json.dumps({"result": "HRV\n52 ms\n- 2026-10-12: 57.2"})),
                        json.dumps(json.dumps({"result": "x", "more": 1})))
    assert evidence.parse_stream(other)["calls"][0]["result"] == json.dumps({"result": "x", "more": 1})


# ── found in the review ──────────────────────────────────────────────────────

def test_a_tool_result_saying_rate_limit_does_not_make_a_failed_run_wait_for_quota(
        stream_cli, tmp_path, monkeypatch):
    """With the whole session on stdout, the quota check read the tools' text
    too: Garmin's own "rate limit?" in a sync answer, plus any failure of the
    run, set the job waiting and re-running for an hour - pulling a Garmin
    account that was already throttling."""
    out = _stream([("sync_garmin", {}, "Garmin fetch not possible right now (GarthHTTPError, "
                                       "rate limit?) - retry later.")], CARD)
    out = out.replace('"is_error": false', '"is_error": true').replace(
        json.dumps(json.dumps(CARD)), json.dumps("API Error: 529 overloaded"))
    d = stream_cli(out, rc=1)
    monkeypatch.setattr(agent, "QUOTA_SLICE_S", 5)
    monkeypatch.setattr(agent, "QUOTA_WAIT_S", 10)
    job = jobs.new_job("x", title="t", kind="train-today")
    done = agent.run(job, db=str(tmp_path / "t.db"))
    assert done["status"] == "failed"
    assert (d / "runs.txt").read_text(encoding="utf-8").count("run") == 1


def test_a_real_quota_answer_on_the_stream_still_waits(stream_cli, tmp_path, monkeypatch):
    out = "\n".join([json.dumps({"type": "system", "subtype": "init"}),
                     json.dumps({"type": "result", "is_error": True, "num_turns": 1,
                                 "result": "You've hit your 5-hour limit · resets 1pm"})])
    d = stream_cli(out, rc=1)
    monkeypatch.setattr(agent, "QUOTA_SLICE_S", 5)
    monkeypatch.setattr(agent, "QUOTA_WAIT_S", 5)
    agent.run(jobs.new_job("x", title="t", kind="train-today"), db=str(tmp_path / "t.db"))
    assert (d / "runs.txt").read_text(encoding="utf-8").count("run") == 2


def test_the_model_cannot_write_its_own_record_onto_the_card(stream_cli, tmp_path, monkeypatch):
    """`evidence` and `sources` are the app's to set. A card that brought its
    own showed "all 5 numbers found there" with no record behind it."""
    forged = {**CARD, "evidence": {"recorded": True, "reads": 7, "numbers": 5, "in_data": 5,
                                   "unmatched": []},
              "sources": [{"kind": "measured", "called": True, "unmatched": []}]}
    stream_cli(_stream([("get_training_readiness", {}, "HRV 52 ms (baseline 61, 9 under)")], forged))
    job = jobs.new_job("x", title="t", kind="train-today")
    agent.run(job, db=str(tmp_path / "t.db"))
    card = jobs.read_card(job["id"])
    assert card["evidence"]["reads"] == 1 and card["sources"][2]["called"] is False

    # ...and when the record step fails, neither the model's nor unchecked marks stay
    def boom(**_kw):
        raise OSError("disk full")
    monkeypatch.setattr(evidence, "card_record", boom)
    job = jobs.new_job("x", title="t", kind="train-today")
    agent.run(job, db=str(tmp_path / "t.db"))
    card = jobs.read_card(job["id"])
    assert "evidence" not in card and "sources" not in card
    assert card["bullets"][0] == "HRV 52 ms, 9 under your baseline of 61"


def test_an_absurd_number_cannot_break_the_check():
    huge = "1" * 5000 + ":00"
    r = evidence.check({"headline": f"at {huge}", "bullets": [huge]}, data="")
    assert r["numbers"] == 0


def test_a_record_whose_card_is_gone_is_swept():
    job = jobs.new_job("x", title="t", kind="train-today")
    jobs.write_evidence(job["id"], {"version": 1})            # its card was never written
    kept = jobs.new_job("x", title="t", kind="train-today")
    jobs.write_card(kept["id"], {"kind": "train-today", "headline": "h", "bullets": [], "verdict": "v"})
    jobs.write_evidence(kept["id"], {"version": 1})
    jobs.cleanup_cards()
    assert not jobs.evidence_path(job["id"]).exists()
    assert jobs.evidence_path(kept["id"]).exists()


def test_the_chat_record_caps_the_arguments_and_prunes_while_it_runs(monkeypatch):
    """Pruned only when a server started, a Claude Desktop that keeps one
    server for weeks kept every day - while the page says 14."""
    old = evidence.chats_dir() / f"{datetime.now() - timedelta(days=evidence.CHAT_DAYS + 3):%Y-%m-%d}-1.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(evidence, "_pruned_on", None)
    evidence.record_chat_call("check_in", {"note": "x" * 50_000}, ok=True, ms=1, result="ok")
    assert not old.exists()
    line = next(evidence.chats_dir().glob(f"{datetime.now():%Y-%m-%d}-*.jsonl")).read_text(encoding="utf-8")
    assert len(line) < 5_000


def test_the_cards_line_counts_every_number_not_found_not_only_the_listed_ones():
    """The card keeps the first 12 numbers not found; the line beside "How this
    card was made" counted only those and said 18 of 30 where 12 was right."""
    card = {"headline": "h", "bullets": [" ".join(str(100 + i) for i in range(30))]}
    doc, summary, _ = evidence.card_record(
        job_id="j-x", model="", stream=evidence.parse_stream(_stream([("get_trend", {}, "100 101 102 "
        "103 104 105 106 107 108 109 110 111")], {})), card=card, sources=[None], inputs={},
        data_rules="", data_context="")
    assert summary["numbers"] == 30 and summary["missing"] == 18 and len(summary["unmatched"]) == 12
    assert doc["check"]["missing"] == 18


def test_the_job_log_of_a_card_keeps_the_answer_not_the_whole_session(stream_cli, tmp_path):
    """The record lives with its card and goes with it. The job log, kept 14
    days whatever happens to the card, held every tool result once the run
    printed its whole session - so deleting a card did not delete them."""
    stream_cli(_stream([("get_training_readiness", {}, "HRV 52 ms (baseline 61, 9 under)")], CARD))
    job = jobs.new_job("x", title="t", kind="train-today")
    agent.run(job, db=str(tmp_path / "t.db"))
    log = jobs.log_path(job["id"]).read_text(encoding="utf-8")
    assert "exit=0" in log and '"num_turns": 3' in log           # the answer is still there
    assert "baseline 61, 9 under" not in log and "tool_result" not in log


def test_the_page_lists_no_chat_step_older_than_the_days_it_promises():
    old_day = datetime.now() - timedelta(days=evidence.CHAT_DAYS + 5)
    f = evidence.chats_dir() / f"{old_day:%Y-%m-%d}-1.jsonl"
    f.write_text(json.dumps({"ts": old_day.astimezone().isoformat(), "tool": "check_in",
                             "args": {}, "ok": True}) + "\n", encoding="utf-8")
    assert evidence.recent_chat_calls() == []


def test_the_cli_s_error_event_still_counts_as_its_own_words(stream_cli, tmp_path, monkeypatch):
    """An error result event carries its text in `errors`, not `result`; the
    quota check read the whole answer before, and still must."""
    out = "\n".join([json.dumps({"type": "system", "subtype": "init"}),
                     json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                                 "num_turns": 1, "errors": ["You've hit your 5-hour limit · resets 1pm"]})])
    d = stream_cli(out, rc=1)
    monkeypatch.setattr(agent, "QUOTA_SLICE_S", 5)
    monkeypatch.setattr(agent, "QUOTA_WAIT_S", 5)
    agent.run(jobs.new_job("x", title="t", kind="train-today"), db=str(tmp_path / "t.db"))
    assert (d / "runs.txt").read_text(encoding="utf-8").count("run") == 2


def test_a_run_that_dies_after_a_failed_sync_does_not_wait_for_quota(stream_cli, tmp_path, monkeypatch):
    """No final event at all: what the tools said is still not the run's word."""
    out = _stream([("sync_garmin", {}, "Garmin fetch not possible right now (rate limit?)")], CARD)
    out = "\n".join(line for line in out.splitlines() if '"type": "result"' not in line)
    d = stream_cli(out, rc=1)
    monkeypatch.setattr(agent, "QUOTA_SLICE_S", 5)
    monkeypatch.setattr(agent, "QUOTA_WAIT_S", 10)
    done = agent.run(jobs.new_job("x", title="t", kind="train-today"), db=str(tmp_path / "t.db"))
    assert done["status"] == "failed"
    assert (d / "runs.txt").read_text(encoding="utf-8").count("run") == 1


def test_a_point_naming_the_tool_by_its_full_name_is_still_checked():
    texts, sources = evidence.split_marks(["[measured: mcp__runspector__get_trend] HRV 52"])
    assert sources[0]["tool"] == "get_trend" and texts == ["HRV 52"]


def test_a_cut_record_does_not_accuse_a_point():
    """Cut by the output cap, the record may lack the very call a point names:
    "the coach did not look at it" would then be the record's gap, not the coach's."""
    out = _stream([("get_trend", {}, "sleep 71")], {})
    stream = evidence.parse_stream("[... 9000 bytes dropped ...]\n" + out)
    card = {"headline": "h", "bullets": ["HRV 52"]}
    src = [{"kind": "measured", "ref": "get_training_readiness", "tool": "get_training_readiness"}]
    _, summary, checked = evidence.card_record(
        job_id="j-x", model="", stream=stream, card=card, sources=src, inputs={},
        data_rules="", data_context="")
    assert summary["complete"] is False
    assert checked[0]["called"] is None and checked[0]["no_data"] is False
    assert checked[0]["unmatched"] == [] and summary["missing"] == 0 and summary["checked"] is False


# ── round 2: the classes closed ──────────────────────────────────────────────

_WHOLE = _stream([("get_trend", {}, "sleep 71")], {})
_SHAPES = {
    "whole stream": _WHOLE,
    "cut stream": "[... 9000 bytes dropped ...]\n" + _WHOLE[30:],
    "one object": json.dumps({"result": "{}", "num_turns": 3}),
    "unreadable line": _WHOLE + "{half an event",
}


@pytest.mark.parametrize("shape", sorted(_SHAPES))
@pytest.mark.parametrize("ref", ["get_trend", "get_training_readiness", None])
@pytest.mark.parametrize("bullet", ["HRV 52", "sleep 71", "no number"])
def test_no_point_is_doubted_unless_the_record_is_a_whole_stream(shape, ref, bullet):
    """One rule for every caller: a point is doubted - a tool not looked at, a
    number not found, no number from the data - only on a record that is the
    whole session. Anything less would turn the record's gap into the coach's."""
    stream = evidence.parse_stream(_SHAPES[shape])
    src = [{"kind": "measured", "ref": ref, "tool": ref}]
    _, summary, checked = evidence.card_record(
        job_id="j-x", model="", stream=stream, card={"headline": "h", "bullets": [bullet]},
        sources=src, inputs={}, data_rules="", data_context="")
    doubted = (checked[0]["called"] is False or checked[0]["no_data"] or checked[0]["unmatched"]
               or summary["missing"] or summary["unmatched"])
    if not evidence.whole(stream):
        assert not doubted and summary["checked"] is False
    else:
        assert summary["checked"] is True
        assert bool(doubted) == (bullet == "HRV 52" or ref == "get_training_readiness")


@pytest.mark.parametrize("rc", [1, -9])
def test_a_run_without_its_final_event_keeps_no_tool_result_in_the_job_log(stream_cli, tmp_path, rc):
    """A crash, a kill, a timeout: no final event, and the raw output went to
    the job log whole - every tool result, for 14 days, outside the record."""
    out = _stream([("get_training_readiness", {}, "HRV 52 ms (baseline 61, 9 under), rate limit?")], CARD)
    out = "\n".join(line for line in out.splitlines() if '"type": "result"' not in line)
    stream_cli(out, rc=rc)
    job = jobs.new_job("x", title="t", kind="train-today")
    done = agent.run(job, db=str(tmp_path / "t.db"))
    assert done["status"] == "failed"
    assert "baseline 61" not in jobs.log_path(job["id"]).read_text(encoding="utf-8")
    assert "baseline 61" not in done["result_summary"]


def test_a_card_run_leaves_no_session_of_its_own_with_claude_code():
    """Claude Code keeps every session it runs, tool results and all, in its
    own folder - outside the record, untouched by deleting a card."""
    assert "--no-session-persistence" in agent.command(Path(evidence.chats_dir()), None)


# ── found by the fresh reader ────────────────────────────────────────────────

@pytest.mark.parametrize("card_says,data", [
    ("VO2max 4x4 on Tue 17 Nov, threshold on Thu 19 Nov, easy on Sat 21 Nov",
     "Tue 2026-11-17 vo2max 4x4 min\nThu 2026-11-19 threshold\nSat 2026-11-21 easy"),
    ("slept 7:13 last night", "sleep 7h 13m"),
    ("the run took 1:09", "duration 1h 09m"),
    ("reps of 4:35", "rep 4m 35s"),
])
def test_a_value_the_coach_writes_the_everyday_way_is_still_found(card_says, data):
    """The tools print a day as 2026-11-17 and a duration as 7h 13m; a card
    says "17 Nov" and "7:13". Read literally, every such point of a planned
    week was marked as not backed by the data - amber where nothing was wrong."""
    r = evidence.check({"headline": card_says, "bullets": []}, data=data)
    assert r["unmatched"] == [], r["unmatched"]


@pytest.mark.parametrize("card_says,data", [("57", "56.5"), ("135", "134.5 km"), ("0.3", "0.25")])
def test_a_number_rounded_the_usual_way_is_found(card_says, data):
    """Python rounds a half to the even neighbour: 56.5 -> 56. A coach writes 57."""
    assert evidence.check({"headline": card_says, "bullets": []}, data=data)["unmatched"] == []


def test_what_the_prompt_already_held_about_the_athlete_counts_as_looked_at(stream_cli, tmp_path):
    """Every card run gets `get_athlete_profile`'s text in its prompt (the
    ABOUT THE ATHLETE block), so the coach cites the tool without calling it -
    and the point was marked as naming a tool it never looked at."""
    card = {**CARD, "bullets": ["[measured: get_athlete_profile] Your highest heart rate is 190 bpm"]}
    stream_cli(_stream([("get_training_readiness", {}, "HRV 52 ms")], card))
    job = jobs.new_job("x", title="t", kind="plan-week")
    agent.run(job, db=str(tmp_path / "t.db"), athlete="- Highest heart rate seen: 190 bpm - on 2026-08-06")
    src = jobs.read_card(job["id"])["sources"][0]
    assert src["called"] is True and src["no_data"] is False and src["unmatched"] == []


def test_the_rules_in_the_prompt_are_looked_at_but_are_not_the_athletes_data():
    stream = evidence.parse_stream(_stream([("get_trend", {}, "sleep 71")], {}))
    src = [{"kind": "measured", "ref": "get_coaching_reference", "tool": "get_coaching_reference"}]
    _, _, checked = evidence.card_record(
        job_id="j-x", model="", stream=stream, card={"headline": "h", "bullets": ["48 hours apart"]},
        sources=src, inputs={}, data_rules="hard stimuli sit 48 hours apart", data_context="",
        given_rules=("get_coaching_reference",))
    assert checked[0]["called"] is True
    assert checked[0]["no_data"] is True            # 48 is the rule's, not the athlete's


# ── found by the fourth reader ───────────────────────────────────────────────

def test_a_crash_with_a_quota_named_skill_in_the_init_event_does_not_wait(stream_cli, tmp_path, monkeypatch):
    """`--verbose` prints an init event listing the user's skills, plugins and
    MCP servers. With no final event that line reached the quota check, and a
    skill called "usage-quota" made a crashed run wait and re-run for an hour."""
    out = "\n".join([json.dumps({"type": "system", "subtype": "init", "skills": ["usage-quota"]}),
                     _event("assistant", {"type": "tool_use", "id": "t1",
                                          "name": "mcp__runspector__get_trend", "input": {}})])
    d = stream_cli(out, rc=1)
    monkeypatch.setattr(agent, "QUOTA_SLICE_S", 5)
    monkeypatch.setattr(agent, "QUOTA_WAIT_S", 10)
    done = agent.run(jobs.new_job("x", title="t", kind="train-today"), db=str(tmp_path / "t.db"))
    assert done["status"] == "failed"
    assert (d / "runs.txt").read_text(encoding="utf-8").count("run") == 1


OLD_CLI_STUB = '''\
import sys
from pathlib import Path
here = Path(__file__).parent
sys.stdin.read()
with open(here / "runs.txt", "a", encoding="utf-8") as fh:
    fh.write("run\\n")
if "--no-session-persistence" in sys.argv:
    sys.stderr.write("error: unknown option '--no-session-persistence'\\n")
    sys.exit(1)
print((here / "out.jsonl").read_text(encoding="utf-8"), end="")
'''


def test_an_older_cli_without_the_session_flag_still_makes_the_card(tmp_path, monkeypatch):
    """An older Claude Code that does not know `--no-session-persistence`
    would fail every card run on the flag alone: the run is made again
    without it, once."""
    d = tmp_path / "old"
    d.mkdir()
    (d / "claude_stub.py").write_text(OLD_CLI_STUB, encoding="utf-8")
    (d / "out.jsonl").write_text(_stream([("get_training_readiness", {}, "HRV 52 ms (baseline 61, 9 under)")],
                                         CARD), encoding="utf-8")
    monkeypatch.setenv("RUNSPECTOR_CLAUDE_CMD", json.dumps([sys.executable, str(d / "claude_stub.py")]))
    monkeypatch.setattr(agent, "_OPTIONAL", dict.fromkeys(agent._OPTIONAL, True))
    job = jobs.new_job("x", title="t", kind="train-today")
    done = agent.run(job, db=str(tmp_path / "t.db"))
    assert done["status"] == "done", done["result_summary"]
    assert (d / "runs.txt").read_text(encoding="utf-8").count("run") == 2


OLDER_CLI_STUB = OLD_CLI_STUB.replace(
    'if "--no-session-persistence" in sys.argv:\n'
    '    sys.stderr.write("error: unknown option \'--no-session-persistence\'\\n")\n'
    '    sys.exit(1)\n',
    'for flag in ("--effort", "--disable-slash-commands"):\n'
    '    if flag in sys.argv:\n'
    '        sys.stderr.write(f"error: unknown option \'{flag}\'\\n")\n'
    '        sys.exit(1)\n')


def test_a_cli_that_knows_none_of_the_newer_options_still_makes_the_card(tmp_path, monkeypatch):
    """`--effort` and `--disable-slash-commands` came later than this app's
    oldest supported Claude Code: each refusal drops that one option, the
    card is still made, and the isolation that CLI does know stays on."""
    assert OLDER_CLI_STUB != OLD_CLI_STUB
    d = tmp_path / "older"
    d.mkdir()
    (d / "claude_stub.py").write_text(OLDER_CLI_STUB, encoding="utf-8")
    (d / "out.jsonl").write_text(_stream([("get_training_readiness", {}, "HRV 52 ms (baseline 61, 9 under)")],
                                         CARD), encoding="utf-8")
    monkeypatch.setenv("RUNSPECTOR_CLAUDE_CMD", json.dumps([sys.executable, str(d / "claude_stub.py")]))
    monkeypatch.setattr(agent, "_OPTIONAL", dict.fromkeys(agent._OPTIONAL, True))
    done = agent.run(jobs.new_job("x", title="t", kind="train-today"), db=str(tmp_path / "t.db"))
    assert done["status"] == "done", done["result_summary"]
    assert (d / "runs.txt").read_text(encoding="utf-8").count("run") == 3
    assert agent._OPTIONAL == {"--no-session-persistence": True, "--setting-sources": True,
                               "--disable-slash-commands": False, "--effort": False}


def test_a_card_run_keeps_the_athletes_own_claude_code_out(tmp_path, monkeypatch):
    """Their CLAUDE.md files, auto memory, hooks and claude.ai connectors stay
    out, the permission mode is the plain one, the effort is the coach's - an
    `auto` mode or an effort in the athlete's settings changed every card."""
    monkeypatch.setattr(agent, "_OPTIONAL", dict.fromkeys(agent._OPTIONAL, True))
    cmd = agent.command(tmp_path, None)
    settings = json.loads(cmd[cmd.index("--settings") + 1])
    assert settings == {"claudeMdExcludes": ["**/CLAUDE.md", "**/CLAUDE.local.md", "**/AGENTS.md",
                                             "**/.claude/rules/**"],
                        "enabledPlugins": {"cc-plugin-agents-md@builtin": False},
                        "autoMemoryEnabled": False, "disableAllHooks": True,
                        "disableClaudeAiConnectors": True, "language": "english", "outputStyle": "default",
                        "permissions": {"defaultMode": "default"}, "agent": "", "ultracode": False,
                        "env": {"CLAUDE_CODE_EFFORT_LEVEL": "medium", "CLAUDE_CODE_EXTRA_BODY": "{}",
                                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "", "CLAUDE_CODE_DISABLE_THINKING": ""}}
    assert cmd[cmd.index("--setting-sources") + 1] == "", "a plan sign-in: no settings file is read"
    assert cmd[cmd.index("--effort") + 1] == agent.COACH_EFFORT == "medium"
    assert "--disable-slash-commands" in cmd and "--no-session-persistence" in cmd
    monkeypatch.setenv("RUNSPECTOR_EFFORT", "high")
    cmd = agent.command(tmp_path, None)
    assert cmd[cmd.index("--effort") + 1] == "high", "the override reaches both places"
    assert json.loads(cmd[cmd.index("--settings") + 1])["env"]["CLAUDE_CODE_EFFORT_LEVEL"] == "high"
    # A typo is no override: the CLI only warns and falls back to ITS default,
    # which is the athlete's setting again.
    monkeypatch.setenv("RUNSPECTOR_EFFORT", "hgh")
    cmd = agent.command(tmp_path, None)
    assert cmd[cmd.index("--effort") + 1] == "medium", "an unknown effort keeps the app's own"


def test_a_sign_in_that_may_live_in_the_settings_keeps_them_read(tmp_path, monkeypatch):
    """A key, a cloud provider or a plan token can sit in the athlete's
    settings files: leaving those unread would sign the card out. The
    overrides hold instead - and their rules folder is excluded where it is,
    also when it is not called .claude."""
    monkeypatch.setattr(agent, "_OPTIONAL", dict.fromkeys(agent._OPTIONAL, True))
    monkeypatch.setattr(agent, "settings_can_stay_unread", lambda **_: False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/srv/claude-config/")
    cmd = agent.command(tmp_path, None)
    assert "--setting-sources" not in cmd
    settings = json.loads(cmd[cmd.index("--settings") + 1])
    assert "/srv/claude-config/rules/**" in settings["claudeMdExcludes"]
    assert settings["agent"] == "" and settings["env"]["CLAUDE_CODE_EXTRA_BODY"] == "{}"
    assert "--setting-sources" in agent.command(tmp_path, None, unread_settings=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "D:\\tools\\cfg")
    cmd = agent.command(tmp_path, None)
    assert "D:/tools/cfg/rules/**" in json.loads(cmd[cmd.index("--settings") + 1])["claudeMdExcludes"]


def test_the_refused_option_is_the_one_the_error_names():
    """Claude Code adds "(Did you mean --x?)" under an unknown option: the flag
    to drop is the one refused, never one the suggestion names."""
    err = "error: unknown option '--sessions'\n(Did you mean --no-session-persistence?)\n"
    assert agent._refused_option(1, err) is None
    assert agent._refused_option(1, "error: unknown option '--effort'\n") == "--effort"
    assert agent._refused_option(0, "error: unknown option '--effort'\n") is None


def test_a_cli_that_refuses_the_isolation_fails_the_card_with_a_word_to_update(tmp_path, monkeypatch):
    """`--settings` carries the isolation: dropped, the card would run with the
    athlete's own CLAUDE.md, memory and permission mode. It is not optional -
    the card fails and says what to do."""
    d = tmp_path / "ancient"
    d.mkdir()
    (d / "claude_stub.py").write_text(OLD_CLI_STUB.replace("--no-session-persistence", "--settings"),
                                      encoding="utf-8")
    (d / "out.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setenv("RUNSPECTOR_CLAUDE_CMD", json.dumps([sys.executable, str(d / "claude_stub.py")]))
    monkeypatch.setattr(agent, "_OPTIONAL", dict.fromkeys(agent._OPTIONAL, True))
    done = agent.run(jobs.new_job("x", title="t", kind="train-today"), db=str(tmp_path / "t.db"))
    assert done["status"] == "failed"
    summary = done["result_summary"]
    assert "does not know --settings" in summary and "claude update" in summary
    assert "runspector needs an update" in summary, "a NEWER CLI that renamed it is the other way round"
    assert (d / "runs.txt").read_text(encoding="utf-8").count("run") == 1, "not run again without it"


# ── a card the model closed with the wrong bracket ───────────────────────────

def test_a_card_whose_list_is_closed_with_a_brace_is_still_read():
    """Seen on a real analyze-run card: every point starts with a "[...]" mark
    now, and the model closed the list of points with `}` instead of `]` - the
    whole, correct card was lost as "no JSON card in the answer"."""
    answer = ('{"kind":"analyze-run","ref":"1","headline":"A 6.0 km easy run.",'
              '"bullets":["[measured: analyze_workout] 6.0 km in 39 min","[derived] steady, not intervals."},'
              '"verdict":"An easy run."}')
    card, problem = agent.parse_card(answer)
    assert problem == "" and card["bullets"] == ["[measured: analyze_workout] 6.0 km in 39 min",
                                                 "[derived] steady, not intervals."]
    assert card["verdict"] == "An easy run."


def test_the_repair_leaves_a_sound_card_and_the_text_inside_its_strings_alone():
    sound = '{"headline":"h","bullets":["a } b ] c"],"verdict":"v"}'
    assert agent.parse_card("Here: " + sound)[0] == json.loads(sound)
    broken = '{"headline":"h","bullets":["[x] keeps } and ] in text"},"verdict":"v"}'
    assert agent.parse_card(broken)[0]["bullets"] == ["[x] keeps } and ] in text"]
    assert agent.parse_card("no card here [ } ]")[0] is None


# ── "analyze this run" on a run the sync never fetched in detail ────────────

class _ZonesClient:
    def get_activity_hr_in_timezones(self, _id):
        return [{"zoneNumber": n, "secsInZone": s, "zoneLowBoundary": lo}
                for n, s, lo in ((1, 300, 100), (2, 1500, 120), (3, 600, 140), (4, 0, 156), (5, 0, 176))]

    def get_activity_typed_splits(self, _id):
        return {"splits": [{"type": "RWD_RUN", "duration": 2400, "distance": 6000, "averageHR": 141}]}

    def get_activity_weather(self, _id):
        return {"temp": 59}

    def get_activity_details(self, _id, **_kw):
        return {}


def test_analysing_an_older_run_fetches_its_zones_and_laps_first(store, today, monkeypatch):
    """The sync fetches zones and laps for recent runs only; a run from August
    was analysed without them - "Garmin recorded no splits, no time in zone"."""
    from conftest import make_activity
    from runspector import garmin, tools

    aid = 9001
    store.upsert_activity(make_activity(aid, today, activity_type="running"))
    monkeypatch.setattr(garmin, "login", lambda *a, **k: _ZonesClient())
    out = tools.analyze_workout(store, activity_id=aid)
    assert "fetched from Garmin just now" in out
    assert "Time in zone: Z1 5m / Z2 25m / Z3 10m" in out
    assert store.get_workout_analysis(activity_id=aid)["has_detail"] is True


def test_a_failed_fetch_is_said_and_the_analysis_still_comes(store, today, monkeypatch):
    from conftest import make_activity
    from runspector import garmin, tools

    aid = 9002
    store.upsert_activity(make_activity(aid, today, activity_type="running"))

    def no_login(*_a, **_k):
        raise RuntimeError("Garmin login failed")
    monkeypatch.setattr(garmin, "login", no_login)
    out = tools.analyze_workout(store, activity_id=aid)
    assert "could not be fetched from Garmin (RuntimeError)" in out
    assert out.startswith("Analysis ")


def test_the_demo_never_asks_garmin(store, today, monkeypatch):
    from conftest import make_activity
    from runspector import garmin, tools

    monkeypatch.setenv("RUNSPECTOR_DEMO", "1")
    store.upsert_activity(make_activity(9003, today, activity_type="running"))
    monkeypatch.setattr(garmin, "login", lambda *a, **k: pytest.fail("the demo asked Garmin"))
    assert "fetched" not in tools.analyze_workout(store, activity_id=9003)


# ── the review of the detail fetch ───────────────────────────────────────────

class _Zones500(_ZonesClient):
    def get_activity_hr_in_timezones(self, _id):
        raise RuntimeError("500 Server Error")


class _NoDetail(_ZonesClient):
    def get_activity_hr_in_timezones(self, _id):
        return []

    def get_activity_typed_splits(self, _id):
        return {"splits": []}


def _fresh_tools(monkeypatch):
    from runspector import tools
    monkeypatch.delenv("RUNSPECTOR_UNATTENDED", raising=False)
    monkeypatch.setattr(tools, "_DETAIL_TRIED", {})
    return tools


def test_a_failed_zone_request_is_not_reported_as_a_run_without_zones(store, today, monkeypatch):
    """A 500 on the zones request read as "Garmin sent no zones or laps for
    this run" - and the coach passed that on as a measured fact."""
    from conftest import make_activity
    from runspector import garmin
    tools = _fresh_tools(monkeypatch)
    store.upsert_activity(make_activity(9101, today, activity_type="running"))
    monkeypatch.setattr(garmin, "login", lambda *a, **k: _Zones500())
    out = tools.analyze_workout(store, activity_id=9101)
    assert "did not answer" in out and "no zones or laps" not in out


def test_a_run_garmin_has_no_detail_for_is_said_so_and_asked_once(store, today, monkeypatch):
    from conftest import make_activity
    from runspector import garmin
    tools = _fresh_tools(monkeypatch)
    store.upsert_activity(make_activity(9102, today, activity_type="strength_training"))
    logins = []
    monkeypatch.setattr(garmin, "login", lambda *a, **k: logins.append(1) or _NoDetail())
    out = tools.analyze_workout(store, activity_id=9102)
    assert "has no zones or laps" in out and "fetched from Garmin just now" not in out
    tools.analyze_workout(store, activity_id=9102)
    assert len(logins) == 1, "asked Garmin again for a run it has nothing for"


def test_analysing_a_day_fetches_that_days_run(store, today, monkeypatch):
    """`coach.md` has the model pass a day; that path only ever looked at runs
    already fetched in detail, so it never fetched anything."""
    from conftest import make_activity
    from runspector import garmin
    tools = _fresh_tools(monkeypatch)
    store.upsert_activity(make_activity(9103, today, activity_type="running"))
    monkeypatch.setattr(garmin, "login", lambda *a, **k: _ZonesClient())
    out = tools.analyze_workout(store, day=today.isoformat())
    assert "fetched from Garmin just now" in out and "Z2 25m" in out


def test_a_card_run_never_logs_in_to_garmin_itself(store, today, monkeypatch):
    """The app's own sync runs in the server; two logins at once on one account
    is what `auto_sync` keeps card runs out of. The server fetches first."""
    from conftest import make_activity
    from runspector import garmin
    tools = _fresh_tools(monkeypatch)
    monkeypatch.setenv("RUNSPECTOR_UNATTENDED", "1")
    store.upsert_activity(make_activity(9104, today, activity_type="running"))
    monkeypatch.setattr(garmin, "login", lambda *a, **k: pytest.fail("a card run logged in"))
    assert "fetched" not in tools.analyze_workout(store, activity_id=9104)


def test_the_server_fetches_a_runs_detail_before_its_analyze_card(tmp_path, today, monkeypatch):
    from conftest import make_activity
    from runspector import garmin, paths
    monkeypatch.setenv("RUNSPECTOR_HOME", str(tmp_path))
    app = server.App.__new__(server.App)
    app.demo, app.token, app._token_fails = False, None, 0
    app.db_path = str(tmp_path / "t.db")
    app.store = Store(app.db_path)
    app.reset_runtime_state()
    app.store.upsert_activity(make_activity(9105, today, activity_type="running"))
    monkeypatch.setattr(paths, "garmin_session_present", lambda: True)
    monkeypatch.setattr(garmin, "login", lambda *a, **k: _ZonesClient())
    job = {"id": "j-x", "template_id": "analyze-run", "ctx": {"activity_id": "9105"}}
    app.prefetch_detail(job)
    assert app.store.get_workout_analysis(activity_id=9105)["has_detail"] is True
    app.demo = True                                      # the demo never asks Garmin
    app.store.upsert_activity(make_activity(9106, today, activity_type="running"))
    monkeypatch.setattr(garmin, "login", lambda *a, **k: pytest.fail("the demo asked Garmin"))
    app.prefetch_detail({"id": "j-y", "template_id": "analyze-run", "ctx": {"activity_id": "9106"}})


def test_an_extra_closing_bracket_does_not_cost_the_cards_proposal():
    answer = ('{"headline":"h","bullets":["a"],"verdict":"Easy."],'
              '"proposal":"p-20261101-101010-abcd"}')
    card, _ = agent.parse_card(answer)
    assert card["proposal"] == "p-20261101-101010-abcd"


# ── found by the fresh reader of the fix ─────────────────────────────────────

def test_a_failed_login_is_tried_again_after_the_athlete_signs_in(store, today, monkeypatch):
    """The "asked once" memory held failures too: after `runspector login` the
    same chat never fetched again, and the analysis came without zones."""
    from conftest import make_activity
    from runspector import garmin
    tools = _fresh_tools(monkeypatch)
    store.upsert_activity(make_activity(9201, today, activity_type="running"))
    logins = []

    def login(*_a, **_k):
        logins.append(1)
        if len(logins) == 1:
            raise RuntimeError("Garmin login failed")
        return _ZonesClient()
    monkeypatch.setattr(garmin, "login", login)
    assert "could not be fetched" in tools.analyze_workout(store, activity_id=9201)
    assert "fetched from Garmin just now" in tools.analyze_workout(store, activity_id=9201)
    assert len(logins) == 2


def test_a_run_without_detail_says_so_where_nothing_is_fetched(store, today, monkeypatch):
    """In a card run nothing is fetched; the analysis showed only dashes."""
    from conftest import make_activity
    tools = _fresh_tools(monkeypatch)
    monkeypatch.setenv("RUNSPECTOR_UNATTENDED", "1")
    store.upsert_activity(make_activity(9202, today, activity_type="running"))
    out = tools.analyze_workout(store, day=today.isoformat())
    assert "No zones or laps are stored for this run" in out


def test_the_server_does_not_log_in_while_its_own_sync_still_runs(tmp_path, today, monkeypatch):
    from conftest import make_activity
    from runspector import garmin, paths
    monkeypatch.setenv("RUNSPECTOR_HOME", str(tmp_path))
    app = server.App.__new__(server.App)
    app.demo, app.token, app._token_fails = False, None, 0
    app.db_path = str(tmp_path / "t.db")
    app.store = Store(app.db_path)
    app.reset_runtime_state()
    app.store.upsert_activity(make_activity(9203, today, activity_type="running"))
    monkeypatch.setattr(paths, "garmin_session_present", lambda: True)
    monkeypatch.setattr(app, "refresh_status", lambda: {"state": "running"})
    monkeypatch.setattr(server.time, "sleep", lambda _s: None)
    monkeypatch.setattr(garmin, "login", lambda *a, **k: pytest.fail("logged in beside a running sync"))
    app.prefetch_detail({"id": "j-z", "template_id": "analyze-run", "ctx": {"activity_id": "9203"}})


class _EmptyRun(_ZonesClient):
    def get_activity_hr_in_timezones(self, _id):
        return []

    def get_activity_typed_splits(self, _id):
        return {"splits": []}


def test_an_empty_answer_for_a_run_means_not_yet_and_is_asked_again_later(store, today, monkeypatch):
    """Garmin can answer empty for a run it is still processing; the sync takes
    that as "retry". Said as "has no zones" and kept for the whole process, the
    run was never asked about again."""
    from conftest import make_activity
    from runspector import garmin
    tools = _fresh_tools(monkeypatch)
    store.upsert_activity(make_activity(9301, today, activity_type="running"))
    clients = [_EmptyRun(), _ZonesClient()]
    logins = []
    monkeypatch.setattr(garmin, "login", lambda *a, **k: logins.append(1) or clients[len(logins) - 1])
    clock = [1000.0]
    monkeypatch.setattr(tools.time, "monotonic", lambda: clock[0])
    first = tools.analyze_workout(store, activity_id=9301)
    assert "sent no zones or laps for this run" in first and "for this activity" not in first
    tools.analyze_workout(store, activity_id=9301)
    assert len(logins) == 1, "asked again at once"
    clock[0] += tools.DETAIL_RETRY_S + 1
    assert "fetched from Garmin just now" in tools.analyze_workout(store, activity_id=9301)


def test_an_unknown_activity_id_is_not_answered_with_another_run_of_that_day(store, today, monkeypatch):
    from conftest import make_activity
    tools = _fresh_tools(monkeypatch)
    monkeypatch.setenv("RUNSPECTOR_UNATTENDED", "1")
    store.upsert_activity(make_activity(9302, today, activity_type="running"))
    out = tools.analyze_workout(store, day=today.isoformat(), activity_id=999)
    assert out.startswith("No run with detail data found")
