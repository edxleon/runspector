"""How each run was run (`logic.run_shape`), whether Garmin's zones can be
trusted for it (`logic.zones_plausible`), and the count over blocks of runs the
coach gets (`facts.run_spread`) - roadmap item 22."""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import make_activity, make_split, split_dict
from runspector import facts, logic, paths

MIN = 60


def _run(z1=0, z2=0, z3=0, z4=0, z5=0, *, duration=None, kind="running") -> dict:
    zones = {f"hr_z{i}_s": v * MIN for i, v in enumerate((z1, z2, z3, z4, z5), start=1)}
    return {"activity_type": kind, "duration_s": (duration or (z1 + z2 + z3 + z4 + z5)) * MIN, **zones}


def _reps(n: int, rep_min: float, rest_min: float = 2) -> dict:
    laps = [split_dict(0, "INTERVAL_WARMUP", 600)]
    for i in range(n):
        laps.append(split_dict(len(laps), "INTERVAL_ACTIVE", int(rep_min * MIN)))
        if i < n - 1:
            laps.append(split_dict(len(laps), "INTERVAL_RECOVERY", int(rest_min * MIN)))
    laps.append(split_dict(len(laps), "INTERVAL_COOLDOWN", 600))
    return logic.interval_facts(laps)


STEADY = logic.interval_facts([split_dict(0, "RWD_RUN", 2400)])


@pytest.mark.parametrize("run,laps,shape", [
    (_run(z1=5, z2=35), STEADY, "easy"),                          # 100 % zones 1-2
    (_run(z2=30, z3=10), STEADY, "easy"),                         # 25 % zone 3: the list says easy too
    (_run(z2=25, z3=15), STEADY, "middle"),                       # 38 % zone 3 - drifted up
    (_run(z2=8, z3=30, z4=2), STEADY, "middle"),                  # the run at 160, every time
    (_run(z2=15, z3=5, z4=25), STEADY, "hard"),                   # a tempo block
    (_run(z2=25, z4=16, z5=4), _reps(5, 4), "hard"),              # 5x4 that got there
    (_run(z2=30, z3=14, z4=1), _reps(5, 4), "middle"),            # 5x4 that never got hard
    (_run(z2=35, z3=5), _reps(10, 1.5, 1), None),                 # short reps: the wrist lags
    (_run(z2=5), STEADY, None),                                   # too little zone time
    (_run(z2=15, duration=60), STEADY, None),                     # zone time for a quarter only
    (_run(z2=40, kind="cycling"), STEADY, None),                  # not a run
])
def test_a_run_is_read_by_its_own_zone_time(run, laps, shape):
    got = logic.run_shape(run, laps)
    assert (got or {}).get("shape") == shape, got


def test_the_training_effect_does_not_make_a_middling_run_hard():
    """Garmin's aerobic TE called 15 of 17 such runs "hard" in one real month."""
    run = {**_run(z2=8, z3=30, z4=2), "aerobic_te": 3.4, "anaerobic_te": 0.3}
    assert logic.is_hard(run)                                   # what the planner counts
    assert logic.run_shape(run, STEADY)["shape"] == "middle"     # what the run was


# ── the count the coach gets ─────────────────────────────────────────────────

def _store_runs(store, today, shapes: list[str]) -> None:
    """Runs oldest first, one every other day back from today, by shape."""
    zones = {"easy": (5, 35, 0, 0, 0), "middle": (0, 8, 30, 2, 0), "hard": (0, 15, 5, 25, 0)}
    for i, shape in enumerate(shapes):
        aid = 100 + i
        day = today - timedelta(days=2 * (len(shapes) - i))
        store.upsert_activity(make_activity(aid, day))
        z = zones[shape]
        store.update_activity_detail(aid, {f"hr_z{k}_s": v * MIN for k, v in enumerate(z, start=1)}
                                     | {"hr_z4_low": 169, "hr_z5_low": 184})
        store.upsert_activity_splits(aid, [make_split(aid, 0, "RWD_RUN", 2400)])


def _spread(store, today):
    return {f.key: f for f in facts.run_spread(store, today, today.isoformat())}


def test_the_coach_sees_the_habit_and_how_it_changed(store, today):
    """The runner who ran every run in the middle for a month, then easy and
    hard apart: the count says what the last block was and what the first was."""
    _store_runs(store, today, ["middle"] * 10 + ["easy", "easy", "hard"] * 4)
    f = _spread(store, today)["spread"]
    assert f.value.startswith("last 10: easy and hard kept apart")
    assert "mostly in the middle - 10 in the middle" in f.change


def test_too_few_judged_runs_say_so(store, today):
    store.upsert_activity(make_activity(1, today))
    _store_runs(store, today, ["easy", "middle"])
    f = _spread(store, today)["spread"]
    assert f.value == "not judged yet" and "2 of 3 runs" in f.evidence


def test_no_runs_no_fact(store, today):
    assert _spread(store, today) == {}


def test_runs_are_not_judged_by_zones_set_too_low(store, today):
    """Zones from an age formula make every run read wrong (zones.md)."""
    _store_runs(store, today, ["easy"] * 6)
    with store._conn() as conn:
        conn.execute("UPDATE activities SET max_hr = 190, hr_z4_low = 120")
    f = _spread(store, today)["spread"]
    assert f.value == "not judged yet"
    assert "6 runs left out" in f.evidence and "Garmin Connect" in f.evidence


def test_a_rep_session_that_never_got_hard_counts_in_the_middle(store, today):
    """Laps do not say a session was MEANT hard (a run/walk plan has them too):
    a rep session that never reached zone 4 is read by its zone time, and so
    shows where the spread is missing - in the middle."""
    _store_runs(store, today, ["easy"] * 6)
    store.upsert_activity(make_activity(200, today - timedelta(days=1)))
    store.update_activity_detail(200, {"hr_z2_s": 30 * MIN, "hr_z3_s": 14 * MIN, "hr_z4_s": MIN})
    laps = [make_split(200, 0, "INTERVAL_WARMUP", 600)]
    for _ in range(5):
        laps.append(make_split(200, len(laps), "INTERVAL_ACTIVE", 240))
        laps.append(make_split(200, len(laps), "INTERVAL_RECOVERY", 120))
    store.upsert_activity_splits(200, laps)
    f = _spread(store, today)["spread"]
    assert "1 in the middle" in f.value and "reps" not in _spread(store, today)


@pytest.mark.parametrize("block,says", [
    (["middle"] * 6 + ["easy"] * 4, "mostly in the middle"),
    (["easy"] * 6 + ["hard"] * 4, "easy and hard kept apart"),
    # the default week (2 hard of 4 runs) seen on any weekday: 50/50 and 40/60 by
    # count are still easy and hard apart - by TIME the hard part is a fifth
    (["easy"] * 5 + ["hard"] * 5, "easy and hard kept apart"),
    (["easy"] * 4 + ["hard"] * 6, "easy and hard kept apart"),
    (["easy"] * 3 + ["hard"] * 7, "mostly hard - hardly an easy run"),
    (["hard"] * 9 + ["middle"], "mostly hard - hardly an easy run"),
    (["easy"] * 6 + ["middle"] * 3 + ["hard"], "easy and hard kept apart"),
    (["easy"] * 10, "mostly easy"),
    (["easy"] * 4 + ["hard"] * 2 + ["middle"] * 4, "mixed"),
])
def test_a_block_of_runs_in_three_words(block, says):
    """Hard sessions that never got hard are not "easy and hard kept apart"."""
    assert facts._mostly(block) == says


# ── the review of item 22 ────────────────────────────────────────────────────

def _laps(*blocks: tuple[str, float]) -> dict:
    out = []
    for kind, minutes in blocks:
        out.append(split_dict(len(out), kind, int(minutes * MIN)))
    return logic.interval_facts(out)


@pytest.mark.parametrize("run,laps,shape", [
    # warm-up / main / cool-down, all easy: a structured EASY run
    (_run(z1=5, z2=45), _laps(("INTERVAL_WARMUP", 10), ("INTERVAL_ACTIVE", 30), ("INTERVAL_COOLDOWN", 10)),
     "easy"),
    # run/walk 8 x (5 min run / 1 min walk), all easy
    (_run(z1=10, z2=38), _laps(*[("INTERVAL_ACTIVE", 5), ("INTERVAL_RECOVERY", 1)] * 8), "easy"),
    # a long run with a block at marathon pace: two long steps, not reps
    (_run(z2=70, z3=20), _laps(("INTERVAL_ACTIVE", 70), ("INTERVAL_ACTIVE", 20)), "easy"),
    (_run(z2=50, z3=40), _laps(("INTERVAL_ACTIVE", 50), ("INTERVAL_ACTIVE", 40)), "middle"),
])
def test_a_structured_run_is_not_a_rep_session_by_its_laps_alone(run, laps, shape):
    """Any run with ACTIVE laps read as a rep session: a run/walk plan or a
    workout with an easy warm-up block was judged as a hard session."""
    assert logic.run_shape(run, laps)["shape"] == shape


@pytest.mark.parametrize("z4,highest,off", [
    (169, 178, False),      # never ran hard: zone 5 above the highest is no sign of wrong zones
    (169, 194, False),
    (130, 194, True),       # zone 4 far below what the runner reaches: set too low
])
def test_zones_are_called_off_only_when_they_are_set_too_low(z4, highest, off):
    assert (logic.zones_plausible(z4, highest) is not None) == off


def test_one_spike_does_not_decide_the_highest_heart_rate(store, today):
    """One optical spike of 225 on a single run turned the check off for months."""
    _store_runs(store, today, ["easy"] * 8)
    with store._conn() as conn:
        # zone 4 at 160 is fine for a highest of ~175 and "too low" for 225 (0.75 x 225 = 169)
        conn.execute("UPDATE activities SET max_hr = 175, hr_z4_low = 160")
        conn.execute("UPDATE activities SET max_hr = 225 WHERE activity_id = 100")
    assert _spread(store, today)["spread"].value.startswith("last 8:")


def test_runs_with_zones_that_look_off_are_left_out_not_the_others(store, today):
    """Each run is judged by its own zone bounds: runs recorded with zones set
    too low are left out; the rest still count - a fix in Garmin Connect never
    reads as a change in how the athlete ran."""
    _store_runs(store, today, ["easy"] * 14)
    with store._conn() as conn:
        conn.execute("UPDATE activities SET max_hr = 190")
        conn.execute("UPDATE activities SET hr_z4_low = 120 WHERE activity_id < 106")   # the old six
    f = _spread(store, today)["spread"]
    assert f.value.startswith("last 8: mostly easy") and f.change is None
    assert "6 runs left out" in f.evidence


def test_the_first_block_never_shares_a_run_with_the_last(store, today):
    """11 runs: the blocks shared 9, and one run decided the change line."""
    _store_runs(store, today, ["easy"] * 6 + ["middle"] * 5)
    f = _spread(store, today)["spread"]
    assert f.change is None


def test_half_is_not_mostly_and_counts_read_plainly():
    assert facts._mostly(["middle"] * 3 + ["easy"] * 3) != "mostly in the middle"
    assert facts._counts(["easy", "middle", "middle"]) == "1 easy, 2 in the middle"


def test_not_judged_yet_says_why(store, today):
    _store_runs(store, today, ["easy", "middle"])                  # all fetched, too few
    assert "more runs" in _spread(store, today)["spread"].evidence
    assert "the sync fetches" not in _spread(store, today)["spread"].evidence


# ── the security review of item 22 ───────────────────────────────────────────

def test_runs_without_zone_time_neither_crash_the_profile_nor_count_as_covered(store, today):
    """A run fetched with laps but no heart-rate zones is stamped as detailed:
    counted as "with detail", the 28-day intensity read 0 seconds as covered -
    and crashed the profile when no run had zone time."""
    from runspector import facts as facts_mod
    for aid in (1, 2, 3):
        store.upsert_activity(make_activity(aid, today - timedelta(days=aid)))
        store.update_activity_detail(aid, {"avg_cadence": 170})          # laps, no zones
    dist = store.get_intensity_distribution(today - timedelta(days=27), today)
    assert dist["with_detail"] == 0 and dist["total_runs"] == 3
    keys = {f.key for f in facts_mod.learned(store, today)}           # no TypeError
    assert "intensity" not in keys


def test_every_run_hard_is_named_as_such():
    """A month of every run at threshold is no spread either - "mixed" hid it."""
    assert facts._mostly(["hard"] * 9 + ["middle"]) == "mostly hard - hardly an easy run"


def test_the_card_prompt_keeps_the_zones_beside_the_new_facts(tmp_path, monkeypatch):
    """The new facts sat before the zones and pushed them out of the card's
    1,200 characters on the demo athlete - the bounds the count is judged by.
    Here with a rep session that stayed in the middle added."""
    from runspector import demo
    from runspector.web import server
    monkeypatch.setenv("RUNSPECTOR_DEMO", "1")
    monkeypatch.setenv("RUNSPECTOR_HOME", str(tmp_path))
    store = demo.seed(tmp_path / "demo.db")
    today = paths.today()
    store.upsert_activity(make_activity(990001, today - timedelta(days=1)))
    store.update_activity_detail(990001, {"hr_z2_s": 1800, "hr_z3_s": 840, "hr_z4_s": 60,
                                          "hr_z4_low": 169, "hr_z5_low": 184})
    laps = [make_split(990001, 0, "INTERVAL_WARMUP", 600)]
    for _ in range(5):
        laps.append(make_split(990001, len(laps), "INTERVAL_ACTIVE", 240))
        laps.append(make_split(990001, len(laps), "INTERVAL_RECOVERY", 120))
    store.upsert_activity_splits(990001, laps)
    # the longest spread line: runs left out for zones set too low, and older
    # runs still coming in
    for i in range(8):
        store.upsert_activity(make_activity(880000 + i, today - timedelta(days=100 + i),
                                            avg_hr=150, max_hr=195))
        store.update_activity_detail(880000 + i, {"hr_z2_s": 1200, "hr_z3_s": 1200,
                                                  "hr_z4_low": 120, "hr_z5_low": 135})
    for i in range(30):
        store.upsert_activity(make_activity(890000 + i, today - timedelta(days=120 + i), avg_hr=150))
    app = server.App.__new__(server.App)
    app.store = store
    text = app.athlete_text()
    assert "runs left out" in text and "30 more runs" in text, "the longest spread line"
    assert "How your runs went" in text and "Heart-rate zones" in text
    assert "more on the You tab" not in text



def test_the_count_and_the_run_list_never_disagree():
    """One band: the list's colour and the count's word for the same run."""
    from runspector import snapshot
    for z in ((0, 30, 10, 0, 0), (0, 25, 15, 0, 0), (0, 15, 5, 25, 0), (0, 8, 30, 2, 0), (10, 30, 0, 0, 0)):
        run = _run(*z)
        listed = snapshot.band({f"z{i}": v for i, v in enumerate(z, start=1)})
        counted = logic.run_shape(run, STEADY)["shape"]
        assert {"easy": "easy", "moderate": "middle", "hard": "hard"}[listed] == counted, z


@pytest.mark.parametrize("avg,shape", [(140, "easy"), (150, "middle")])
def test_a_run_held_in_the_apps_own_easy_band_is_easy(avg, shape):
    """With %-of-max zones the easy band the app puts on the watch (0.83 x the
    zone-5 floor) reaches into zone 3: a runner who keeps to it ran easy, though
    half of it reads as zone 3."""
    run = {**_run(z2=19, z3=21), "avg_hr": avg, "hr_z5_low": 171}      # cap 142
    assert logic.run_shape(run, STEADY)["shape"] == shape


def test_the_planner_and_the_count_share_the_easy_cap():
    from runspector import planning
    target, _ = planning.easy_cap({"z5_low": 171})
    assert target["hi"] == round(logic.EASY_CAP_OF_Z5 * 171)


# ── the fresh reader of item 22 ──────────────────────────────────────────────

#: Garmin's default %-of-max zones for a max of 190: zone 3 from 133, zone 4
#: from 152, zone 5 from 171 - the app's easy cap (0.83 x 171 = 142) is inside zone 3.
ZONES_MAX = {"hr_z4_low": 152, "hr_z5_low": 171}


def _steps(*steps: tuple[str, float, int]) -> dict:
    """Workout steps as laps: (type, minutes, average heart rate)."""
    return logic.interval_facts([split_dict(i, t, int(m * MIN), avg_hr=hr)
                                 for i, (t, m, hr) in enumerate(steps)])


def _reps_hr(n: int, rep_min: float, hr: int) -> dict:
    steps = [("INTERVAL_WARMUP", 10, 118)]
    for i in range(n):
        steps.append(("INTERVAL_ACTIVE", rep_min, hr))
        if i < n - 1:
            steps.append(("INTERVAL_RECOVERY", 2, 122))
    return _steps(*steps, ("INTERVAL_COOLDOWN", 10, 118))


@pytest.mark.parametrize("run,laps,shape", [
    # 5x4 at the top of zone 3: warm-up and recoveries pull the average to 136,
    # under the easy cap - the reps were not easy, and never got hard
    ({**_run(z2=28, z3=20, z4=1), "avg_hr": 136, **ZONES_MAX}, _reps_hr(5, 4, 150), "middle"),
    # a 20-minute tempo step in zone 3 between warm-up and cool-down
    ({**_run(z2=20, z3=20), "avg_hr": 134, **ZONES_MAX},
     _steps(("INTERVAL_WARMUP", 10, 120), ("INTERVAL_ACTIVE", 20, 150), ("INTERVAL_COOLDOWN", 10, 120)),
     "middle"),
    # reps that averaged in zone 4 although the wrist's lag kept the zone time short
    ({**_run(z2=35, z3=12, z4=3), "avg_hr": 138, **ZONES_MAX}, _reps_hr(5, 4, 156), "hard"),
    # run/walk whose running stayed under the cap: easy, as before
    ({**_run(z1=10, z2=20, z3=18), "avg_hr": 136, **ZONES_MAX},
     _steps(*[("INTERVAL_ACTIVE", 5, 138), ("INTERVAL_RECOVERY", 1, 115)] * 8), "easy"),
])
def test_a_rep_session_is_read_by_its_reps_not_the_runs_average(run, laps, shape):
    """The easy-cap reading took the whole run's average: a rep session or a
    tempo at the top of zone 3 counted as easy, because its warm-up and
    recoveries pulled the average under the cap - and the runner whose hard
    days never get hard read "mostly easy"."""
    assert logic.run_shape(run, laps)["shape"] == shape


def test_the_intensity_tool_says_when_zone_3_holds_the_apps_easy_band(store, today):
    """With %-of-max zones the easy cap the app puts on the watch is inside
    zone 3: the tool called the runner who keeps to it "a lot of grey zone"
    while the profile counted the same runs easy."""
    from runspector import tools
    for i in range(1, 7):
        store.upsert_activity(make_activity(i, today - timedelta(days=i), avg_hr=139))
        store.update_activity_detail(i, {"hr_z2_s": 19 * MIN, "hr_z3_s": 21 * MIN, **ZONES_MAX})
    out = tools.get_intensity_distribution(store, period_days=28)
    assert "easy cap of 142 bpm" in out and "Zone 3 starts near 133 bpm" in out
    assert "How your runs went" in out


def test_the_intensity_tool_stays_quiet_when_the_cap_is_below_zone_3(store, today):
    from runspector import tools
    for i in range(1, 7):
        store.upsert_activity(make_activity(i, today - timedelta(days=i)))
        store.update_activity_detail(i, {"hr_z2_s": 10 * MIN, "hr_z3_s": 30 * MIN,
                                         "hr_z4_low": 169, "hr_z5_low": 178})
    assert "easy cap" not in tools.get_intensity_distribution(store, period_days=28)


def test_no_change_line_while_the_history_is_still_coming_in(store, today):
    """Older runs arrive 20 a sync in random order: "your first runs" was
    whichever had arrived, and the line came and went between syncs."""
    _store_runs(store, today, ["middle"] * 10 + ["easy"] * 10)
    assert _spread(store, today)["spread"].change, "complete: the change shows"
    for i in range(5):
        store.upsert_activity(make_activity(900 + i, today - timedelta(days=150 + i), avg_hr=150))
    f = _spread(store, today)["spread"]
    assert f.change is None and "5 more runs" in f.evidence


def test_one_run_that_never_arrives_does_not_hold_the_change_back(store, today):
    """A run deleted on Garmin is never stamped: "waiting" never reached 0, and
    the comparison promised in the evidence never came."""
    _store_runs(store, today, ["middle"] * 10 + ["easy"] * 10)
    store.upsert_activity(make_activity(900, today - timedelta(days=150), avg_hr=150))
    f = _spread(store, today)["spread"]
    assert f.change and "more run" not in f.evidence


def test_a_run_without_heart_rate_is_not_waiting_for_zones(store, today):
    """Entered by hand or run without a sensor: no zones will ever come, and
    "the sync fetches them" never came true."""
    _store_runs(store, today, ["easy", "middle"])
    store.upsert_activity(make_activity(901, today - timedelta(days=60)))     # no heart rate
    f = _spread(store, today)["spread"]
    assert "the sync fetches" not in f.evidence


def test_fixing_the_zones_is_said_to_help_from_then_on(store, today):
    """Garmin keeps a recorded run's zone times: new settings do not clear the line."""
    _store_runs(store, today, ["easy"] * 6)
    with store._conn() as conn:
        conn.execute("UPDATE activities SET max_hr = 190, hr_z4_low = 120")
    assert "already recorded keep" in _spread(store, today)["spread"].evidence


@pytest.mark.parametrize("laps", [
    _steps(("INTERVAL_WARMUP", 10, 125), ("INTERVAL_ACTIVE", 30, 147), ("INTERVAL_COOLDOWN", 10, 125)),
    _steps(*[("INTERVAL_ACTIVE", 5, 146), ("INTERVAL_RECOVERY", 1, 120)] * 8),
])
def test_steps_above_the_cap_but_below_zone_3_stay_easy(laps):
    """With threshold-based zones the easy cap (0.83 x 170 = 141) lies inside
    zone 2: a run kept in zones 1-2 counted "in the middle" because its steps
    averaged 147 - and the run list called it easy."""
    run = {**_run(z1=10, z2=40), "avg_hr": 140, "hr_z4_low": 160, "hr_z5_low": 170}
    assert logic.run_shape(run, laps)["shape"] == "easy"


def test_not_judged_yet_counts_what_is_still_coming(store, today):
    """Runs skipped for too little zone time are no runs the sync still fetches."""
    _store_runs(store, today, ["easy", "middle"])
    store.upsert_activity(make_activity(902, today - timedelta(days=3), duration_s=600))
    store.update_activity_detail(902, {"hr_z2_s": 300})                   # judged? too short
    store.upsert_activity(make_activity(903, today - timedelta(days=60), avg_hr=140))
    ev = _spread(store, today)["spread"].evidence
    assert "the sync fetches zones and laps for 1 more" in ev


def test_an_older_window_is_not_judged_on_the_few_runs_that_have_arrived(store, today):
    """The history fetch fills older 28-day windows 20 runs a sync at random: the
    intensity fact's change line was judged on whichever had arrived, and came,
    went and turned round between syncs. A window is judged once enough of its
    runs carry zones - by the one gate the intensity tool uses too."""
    from runspector import tools
    for i in range(6):
        store.upsert_activity(make_activity(400 + i, today - timedelta(days=2 * i), avg_hr=140))
    store.update_activity_detail(400, {"hr_z2_s": 10 * MIN, "hr_z3_s": 30 * MIN})   # 1 of 6 arrived
    dist = store.get_intensity_distribution(today - timedelta(days=27), today)
    assert facts._grey_bucket(dist) is None
    assert "No judgement on the distribution" in tools.get_intensity_distribution(store, 28)
    for i in range(1, 5):                                                             # 5 of 6
        store.update_activity_detail(400 + i, {"hr_z2_s": 10 * MIN, "hr_z3_s": 30 * MIN})
    dist = store.get_intensity_distribution(today - timedelta(days=27), today)
    assert facts._grey_bucket(dist) == "70-80 % in zone 3"


@pytest.mark.parametrize("stride_hr", [145, 155])
def test_an_easy_run_with_strides_as_steps_stays_easy(stride_hr):
    """The steps' heart rate was a plain mean over the laps: six 20-second
    strides outweighed 30 easy minutes, and "easy + strides" counted in the
    middle or hard. Weighted by time, the run is what its minutes were."""
    run = {**_run(z1=3, z2=30, z3=5), "avg_hr": 136, **ZONES_MAX}
    steps = [("INTERVAL_ACTIVE", 30, 136)]
    for _ in range(6):
        steps += [("INTERVAL_ACTIVE", 20 / 60, stride_hr), ("INTERVAL_RECOVERY", 1, 125)]
    assert logic.run_shape(run, _steps(*steps))["shape"] == "easy"


def test_a_change_of_zones_is_named_beside_the_change_it_makes(store, today):
    """Each run is read by the zones it was recorded with. When Garmin raised
    the zones between the first runs and the last (its max-HR detection only
    goes up), the same running read "in the middle" then and "easy" now - and
    the change line told the runner they changed their habit."""
    for i in range(20):
        aid = 500 + i
        store.upsert_activity(make_activity(aid, today - timedelta(days=2 * (20 - i)), avg_hr=138))
        bounds = {"hr_z4_low": 144, "hr_z5_low": 162} if i < 10 else {"hr_z4_low": 150, "hr_z5_low": 169}
        store.update_activity_detail(aid, {"hr_z2_s": 8 * MIN, "hr_z3_s": 32 * MIN, **bounds})
        store.upsert_activity_splits(aid, [make_split(aid, 0, "RWD_RUN", 2400)])
    f = _spread(store, today)["spread"]
    assert f.change and "zone 4 from 144 bpm then, 150 now" in f.change, f.change


def test_the_same_zones_add_nothing_to_the_change_line(store, today):
    _store_runs(store, today, ["middle"] * 10 + ["easy"] * 10)
    assert "zone 4 from" not in _spread(store, today)["spread"].change
