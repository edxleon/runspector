"""The planned week, seen and changed by the athlete (roadmap 19).

Live use: a week was planned (Mon, Tue, Sat) and the athlete saw it did not
fit - Monday not, Tuesday too long, Saturday rather Friday. The strip showed
three boxes with a name each and no way to know what was behind them or to
change one. The athlete decides; the coach says what it thinks of it.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from conftest import FakeGarmin, make_activity, make_day
from runspector import garmin, plan, planning, snapshot, tools
from runspector.models import ScheduledWorkout

# ── what is behind a box ─────────────────────────────────────────────────────


def test_a_planned_session_says_what_it_is():
    easy = planning.build_session("easy", duration_min=24,
                                  zones={"z5_low": 184})
    d = planning.session_detail(easy)
    assert d["kind"] == "easy" and d["minutes"] == 24
    assert d["km"] == round(easy.estimated_meters / 1000, 1)
    assert d["pace"] and d["pace"].endswith("/km"), d
    assert d["what"].lower().startswith("easy run"), d
    assert d["easier_min"] is None
    vo2 = planning.with_fallback(planning.build_session("vo2max", duration_min=45))
    d = planning.session_detail(vo2)
    assert d["kind"] == "vo2max" and "zone 5" in d["what"], d
    assert any("×" in s for s in d["steps"]), d["steps"]
    assert d["steps"][0].startswith("Warm-up"), d["steps"]
    assert d["easier_min"] == vo2.fallback_min


def test_the_week_strip_carries_what_is_behind_each_box(store, today):
    spec = planning.build_session("easy", duration_min=24)
    store.record_workout(4712, name=spec.name, kind="easy",
                         spec_json=json.dumps(planning.to_json(spec)),
                         schedule_id=91, scheduled_day=today)
    store.replace_scheduled_workouts(
        [ScheduledWorkout(91, today, workout_id=4712, title=spec.name, sport="running"),
         ScheduledWorkout(92, today + timedelta(days=1), workout_id=777, title="Garmin Coach run",
                          sport="running")],
        today - timedelta(days=7), today + timedelta(days=14))
    plan = snapshot.assemble(store, today=today)["plan"]
    entries = {e["title"]: e for d in plan["days"] for e in d["planned"]}
    ours, theirs = entries[spec.name], entries["Garmin Coach run"]
    assert ours["schedule_id"] == 91 and theirs["schedule_id"] == 92
    assert ours["detail"]["minutes"] == 24 and ours["own"] is True
    # A workout the app did not make: its name is all there is, and it says so.
    assert theirs.get("detail") is None and theirs["own"] is False



# ── a change is a proposal: the athlete decides, the coach comments ─────────

def _monday(today: date) -> date:
    """The Monday of the week AFTER `today`'s: every day of it lies ahead."""
    return today - timedelta(days=today.weekday()) + timedelta(weeks=1)


def _own(store, sid: int, day: date, kind: str, minutes: int) -> planning.SessionSpec:
    """A session this app planned and put on the calendar."""
    spec = planning.with_fallback(planning.build_session(kind, duration_min=minutes))
    store.record_workout(4000 + sid, name=spec.name, kind=kind,
                         spec_json=json.dumps(planning.to_json(spec)), schedule_id=sid,
                         scheduled_day=day)
    return spec


def _calendar(store, today: date, entries: list[tuple]) -> None:
    """`(schedule_id, day, title, own)`: the mirror of the Garmin calendar."""
    store.replace_scheduled_workouts(
        [ScheduledWorkout(sid, day, workout_id=(4000 + sid) if own else 9000 + sid, title=title,
                          sport="running") for sid, day, title, own in entries],
        today - timedelta(days=7), today + timedelta(days=14))


def _history(store, monday: date, minutes_per_week=(150, 150, 150, 150)) -> None:
    """Four running weeks before `monday`'s week, two runs each, oldest first."""
    for back, mins in enumerate(reversed(minutes_per_week), start=1):
        start = monday - timedelta(weeks=back)
        for i, (offset, m) in enumerate(((1, mins // 2), (5, mins - mins // 2))):
            aid = 70000 + back * 10 + i
            store.upsert_activity(make_activity(aid, start + timedelta(days=offset),
                                                duration_s=m * 60, distance_m=m * 160,
                                                aerobic_te=2.5, anaerobic_te=0.3, load=60))
            store.update_activity_detail(aid, {"hr_z1_s": 0, "hr_z2_s": m * 60, "hr_z3_s": 0,
                                               "hr_z4_s": 0, "hr_z5_s": 0, "splits": [],
                                               "hr_z4_low": 156, "hr_z5_low": 172})


def _the_return_week(store, today):
    """The live case: Mon, Tue and Sat easy 24 min in the coming week."""
    monday = _monday(today)
    _history(store, monday)
    specs = {sid: _own(store, sid, monday + timedelta(days=off), "easy", 24)
             for sid, off in ((11, 0), (12, 1), (16, 5))}
    _calendar(store, today, [(sid, monday + timedelta(days=off), specs[sid].name, True)
                             for sid, off in ((11, 0), (12, 1), (16, 5))])
    return monday


def test_saturday_moved_to_friday_is_the_same_session_on_the_new_day(store, today):
    monday = _the_return_week(store, today)
    friday, saturday = monday + timedelta(days=4), monday + timedelta(days=5)
    p = plan.propose_change(store, [{"schedule_id": 16, "action": "move", "day": friday.isoformat()}],
                            today=today)
    assert [(it["day"], it["spec"]["kind"]) for it in p["items"]] == [(friday.isoformat(), "easy")]
    assert [(r["schedule_id"], r["day"]) for r in p["replaces"]] == [(16, saturday.isoformat())]
    assert p["items"][0]["spec"]["estimated_seconds"] == 24 * 60
    # The preview says which Friday and which Saturday - the athlete's words
    # name weekdays, the yes is to dates.
    assert f"Fri {friday.isoformat()}" in p["preview"] and saturday.isoformat() in p["preview"]
    assert p["notes"] == [], p["notes"]
    assert p["status"] == "open" and plan.read(p["id"])["notes"] == []


def test_a_session_taken_out_is_a_proposal_with_nothing_to_upload(store, today, monkeypatch):
    monday = _the_return_week(store, today)
    p = plan.propose_change(store, [{"schedule_id": 11, "action": "remove"}], today=today)
    assert p["items"] == [] and [r["schedule_id"] for r in p["replaces"]] == [11]
    assert "Takes off the calendar" in p["preview"] and monday.isoformat() in p["preview"]
    fake = FakeGarmin(schedule={11: {"workoutId": 4011, "date": monday.isoformat()}})
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: fake)
    out = tools.apply_workout(store, p["id"])
    assert 11 not in fake.data["schedule"], out
    assert plan.read(p["id"])["status"] == "applied", "a removal done is applied, not open"
    assert "Removed from the calendar" in out, out
    # ...and it comes back with an undo.
    tools.undo_workout(store, p["id"])
    assert [s["workoutId"] for s in fake.data["schedule"].values()] == [4011]


def test_a_session_made_shorter_or_harder_is_built_anew_on_its_day(store, today):
    monday = _the_return_week(store, today)
    p = plan.propose_change(store, [{"schedule_id": 12, "action": "change", "minutes": 15}],
                            today=today)
    (it,) = p["items"]
    assert it["day"] == (monday + timedelta(days=1)).isoformat()
    assert it["spec"]["kind"] == "easy" and it["spec"]["estimated_seconds"] == 15 * 60
    p = plan.propose_change(store, [{"schedule_id": 12, "action": "change", "kind": "threshold",
                                     "minutes": 45}], today=today)
    assert p["items"][0]["spec"]["kind"] == "threshold"


def test_a_run_added_on_a_free_day(store, today):
    monday = _the_return_week(store, today)
    thursday = monday + timedelta(days=3)
    p = plan.propose_change(store, [{"action": "add", "day": thursday.isoformat(), "kind": "easy",
                                     "minutes": 30}], today=today)
    assert [(it["day"], it["spec"]["kind"]) for it in p["items"]] == [(thursday.isoformat(), "easy")]
    assert p["replaces"] == []


# ── what the coach says about it ─────────────────────────────────────────────

def test_more_than_the_plans_step_is_kept_and_commented(store, today):
    """The athlete is ambitious: a week of 72 min made 198 after weeks of 150.
    Kept - the athlete decides - with the coach's word on it."""
    monday = _the_return_week(store, today)
    p = plan.propose_change(store, [
        {"schedule_id": 12, "action": "change", "minutes": 90},
        {"action": "add", "day": (monday + timedelta(days=3)).isoformat(), "kind": "easy",
         "minutes": 60}], today=today)
    assert len(p["items"]) == 2, "kept as asked"
    (vol,) = [n for n in p["notes"] if "min of running" in n]
    assert "%" in vol and "the plan would give" in vol, vol


def test_two_hard_days_in_a_row_are_kept_and_commented(store, today):
    monday = _monday(today)
    _history(store, monday)
    _own(store, 21, monday + timedelta(days=1), "vo2max", 50)
    _own(store, 23, monday + timedelta(days=3), "threshold", 50)
    _calendar(store, today, [(21, monday + timedelta(days=1), "VO2max", True),
                             (23, monday + timedelta(days=3), "Threshold", True)])
    p = plan.propose_change(store, [{"schedule_id": 23, "action": "move",
                                     "day": (monday + timedelta(days=2)).isoformat()}], today=today)
    assert p["items"][0]["day"] == (monday + timedelta(days=2)).isoformat()
    assert any("48 hours" in n for n in p["notes"]), p["notes"]


def test_taking_out_the_weeks_only_hard_session_says_what_it_was_for(store, today):
    """The other way round: the week loses the stimulus the plan gave it."""
    monday = _monday(today)
    _history(store, monday)
    for sid, off, kind, mins in ((21, 1, "vo2max", 50), (24, 4, "easy", 40), (27, 6, "long", 70)):
        _own(store, sid, monday + timedelta(days=off), kind, mins)
    _calendar(store, today, [(21, monday + timedelta(days=1), "VO2max", True),
                             (24, monday + timedelta(days=4), "Easy", True),
                             (27, monday + timedelta(days=6), "Long", True)])
    p = plan.propose_change(store, [{"schedule_id": 21, "action": "change", "kind": "easy",
                                     "minutes": 40}], today=today)
    assert p["items"][0]["spec"]["kind"] == "easy", "kept as asked"
    (note,) = [n for n in p["notes"] if "no longer holds a hard session" in n]
    assert "VO2max intervals" in note, note


def test_a_hard_session_in_a_return_week_is_kept_and_commented(store, today):
    """Back from a pause the plan runs easy - the live case was 49 days off."""
    monday = _monday(today)
    _history(store, monday - timedelta(weeks=7))           # the last run seven weeks back
    _own(store, 16, monday + timedelta(days=5), "easy", 24)
    _calendar(store, today, [(16, monday + timedelta(days=5), "Easy run 24 min", True)])
    p = plan.propose_change(store, [{"schedule_id": 16, "action": "change", "kind": "vo2max",
                                     "minutes": 45}], today=today)
    assert p["items"][0]["spec"]["kind"] == "vo2max", "kept as asked"
    assert any("return week" in n for n in p["notes"]), p["notes"]


def test_a_run_on_a_day_the_athlete_is_in_pain_is_kept_and_commented(store, today):
    monday = _the_return_week(store, today)
    store.say_check_in("pain", today)
    p = plan.propose_change(store, [{"action": "add", "day": today.isoformat(), "kind": "easy",
                                     "minutes": 30}], today=today)
    assert p["items"], "the athlete decides"
    assert any("pain" in n for n in p["notes"]), p["notes"]
    assert monday


def test_a_run_today_on_a_rest_day_is_kept_and_commented(store, today):
    _the_return_week(store, today)
    store.upsert_daily(make_day(today, hrv_status="LOW", sleep_score=85, body_battery_high=90))
    p = plan.propose_change(store, [{"action": "add", "day": today.isoformat(), "kind": "easy",
                                     "minutes": 30}], today=today)
    assert any("Rest" in n for n in p["notes"]), p["notes"]


# ── what cannot be done ──────────────────────────────────────────────────────

def test_what_cannot_be_changed_says_why(store, today):
    monday = _the_return_week(store, today)
    _calendar(store, today, [(11, monday, "Easy", True), (31, monday + timedelta(days=2),
                                                          "Garmin Coach run", False),
                             (5, today - timedelta(days=1), "Yesterday", True)])
    with pytest.raises(ValueError, match="no change"):
        plan.propose_change(store, [], today=today)
    with pytest.raises(ValueError, match="not on the calendar"):
        plan.propose_change(store, [{"schedule_id": 999, "action": "remove"}], today=today)
    with pytest.raises(ValueError, match="passed"):
        plan.propose_change(store, [{"schedule_id": 5, "action": "remove"}], today=today)
    with pytest.raises(ValueError, match="this app planned"):
        plan.propose_change(store, [{"schedule_id": 31, "action": "move",
                                     "day": (monday + timedelta(days=3)).isoformat()}], today=today)
    with pytest.raises(ValueError, match="beyond"):
        plan.propose_change(store, [{"schedule_id": 11, "action": "move",
                                     "day": (today + timedelta(days=30)).isoformat()}], today=today)
    with pytest.raises(ValueError, match="once"):
        plan.propose_change(store, [{"schedule_id": 11, "action": "remove"},
                                    {"schedule_id": 11, "action": "move",
                                     "day": (monday + timedelta(days=3)).isoformat()}], today=today)
    # A foreign workout can be taken off, or replaced by one of the app's.
    p = plan.propose_change(store, [{"schedule_id": 31, "action": "change", "kind": "easy",
                                     "minutes": 30}], today=today)
    assert p["items"][0]["spec"]["kind"] == "easy" and p["replaces"][0]["schedule_id"] == 31


# ── from the week strip ──────────────────────────────────────────────────────

def _app(store):
    from runspector.web import server

    a = server.App.__new__(server.App)
    a.store, a.demo = store, False
    return a


def test_the_strip_files_a_change_and_the_same_buttons_apply_and_undo_it(store, today,
                                                                       monkeypatch):
    monday = _the_return_week(store, today)
    app = _app(store)
    out, code = app.change_week({"edits": [{"schedule_id": 11, "action": "remove"}]})
    assert code == 200, out
    p = out["proposal"]
    assert p["change"] is True and p["notes"] == [] and p["status"] == "open"
    assert "Takes off the calendar" in p["preview"]
    fake = FakeGarmin(schedule={11: {"workoutId": 4011, "date": monday.isoformat()}})
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: fake)
    out, code = app.apply_proposal({"proposal_id": p["id"]})
    assert code == 200 and out["ok"], out
    assert 11 not in fake.data["schedule"]
    # A change that only took a session off can be taken back too.
    out, code = app.undo_proposal({"proposal_id": p["id"]})
    assert code == 200, out
    assert [s["workoutId"] for s in fake.data["schedule"].values()] == [4011]


def test_the_strip_says_why_a_change_cannot_be_made(store, today):
    _the_return_week(store, today)
    app = _app(store)
    out, code = app.change_week({"edits": [{"schedule_id": 999, "action": "remove"}]})
    assert code == 400 and "not on the calendar" in out["error"], out
    out, code = app.change_week({"edits": "move it"})
    assert code == 400, out
    out, code = app.change_week({"edits": [{"schedule_id": 11, "action": "change",
                                            "kind": {"x": 1}, "minutes": 30}]})
    assert code == 400 and "kind" in out["error"], out


# ── in words, through the coach ──────────────────────────────────────────────

def test_the_coach_files_the_athletes_change_with_its_notes(store, today):
    """"Friday instead of Saturday" in a chat or a card: the coach names the
    entry by its schedule id and the day by its date, and the tool files the
    same proposal the strip does - with the notes the coach must pass on."""
    monday = _the_return_week(store, today)
    friday = monday + timedelta(days=4)
    out = tools.propose_change(store, [{"action": "move", "schedule_id": 16,
                                        "day": friday.isoformat()}])
    assert f"Fri {friday.isoformat()}" in out and "proposal p-" in out, out
    assert "NOT on Garmin" in out and "apply_workout" in out, out
    out = tools.propose_change(store, [{"action": "change", "schedule_id": 12, "minutes": 90},
                                       {"action": "add", "day": (monday + timedelta(days=3)).isoformat(),
                                        "kind": "easy", "minutes": 60}])
    assert "coach:" in out and "the athlete decides" in out, out
    assert tools.propose_change(store, []).startswith("Cannot make that change")


def test_the_calendar_the_coach_reads_names_each_day(store, today):
    """"Saturday" must be a date to the coach: each calendar line carries its
    weekday."""
    monday = _the_return_week(store, today)
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=80, body_battery_high=80))
    out = tools.get_training_readiness(store)
    sat = monday + timedelta(days=5)
    assert f"Sat {sat.isoformat()}" in out, out
