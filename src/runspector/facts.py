"""What the coach has learned about the athlete from their data - layer 1 of
the athlete's memory, computed in code every time it is read.

Stored facts go stale: "you run too much zone 3" is wrong four weeks after
the athlete fixed it, and it keeps steering every answer. So nothing here is
stored. Each fact is the value the planner and the snapshot already compute,
with the evidence it rests on and the day it is as of - and a CHANGE line
when the value moved and has held since ("long run on sat since about 3 weeks
ago, before: sun"). A change counts only when the new value held at two weekly
anchors in a row; one odd week is not a new habit.

The facts go to three places: the "You" tab, the athlete block of every card
prompt (`agent.build_prompt`), and the MCP tool `get_athlete_profile`. They
are context for the coach, not decisions: today's numbers still come from the
tools, and the verdict is still `logic.py`'s.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date, timedelta

from . import habits, logic, paths
from . import profile as profile_mod
from .store import Store

#: How many weekly anchors back a change is looked for.
CHANGE_WEEKS = 8
#: Zone 3 share is tracked in steps of this many percent: 41 % and 43 % are
#: the same pattern, 32 % and 45 % are not.
GREY_STEP = 10
#: How each run was run is counted in blocks of this many judged runs - not in
#: weeks: a runner back after seven weeks off has empty weeks, not empty runs.
SPREAD_BLOCK = 10
#: Fewer judged runs than this in a block say nothing - nor make a change line.
SPREAD_MIN = 6


@dataclass(frozen=True)
class Fact:
    key: str
    label: str
    value: str
    evidence: str
    source: str                 # "your history", "Garmin", "the app"
    as_of: str
    change: str | None = None
    #: The same in plain words, for the You tab - the label, value and evidence
    #: are what the coach reads (`line`); this is what a person reads first.
    plain: str | None = None

    def line(self) -> str:
        text = f"- {self.label}: {self.value} - {self.evidence}"
        return text + (f" ({self.change})" if self.change else "")


def _change(series: list) -> str | None:
    """`series[0]` is now, `series[k]` the value k weeks ago. A change line when
    the value now held at the last two anchors, and the value before it held at
    two anchors too - else one odd week would be reported as a new habit."""
    if len(series) < 3 or series[0] is None or series[1] != series[0]:
        return None
    for k in range(2, len(series)):
        if series[k] != series[0]:
            before = series[k]
            if before is None or (k + 1 < len(series) and series[k + 1] != before):
                return None
            return f"since about {k - 1} week{'s' if k > 2 else ''} ago; before: {before}"
    return None


def _grey_bucket(dist: dict) -> str | None:
    """The window's zone-3 share in steps - None until enough of its runs carry
    zones (`logic.zones_cover`): while the history fetch fills older windows at
    random, a few arrived runs made the change line come and go between syncs."""
    pct = dist.get("moderate_pct")
    if pct is None or not logic.zones_cover(dist):
        return None
    low = int(pct // GREY_STEP) * GREY_STEP
    return f"{low}-{low + GREY_STEP} % in zone 3"


def _intensity_plain(d: dict) -> str:
    """Time in zones in plain words. Time is named by its zone, not as easy /
    moderate / hard runs: with %-of-max zones the app's easy limit reaches into
    zone 3, and ten runs held under it are "10 easy" with half their time in
    zone 3 (`tools.get_intensity_distribution` says the same)."""
    out = (f"{round(d['easy_pct'])} % of your running time in the last 4 weeks was in the easy "
           f"zones 1-2 - about 80 % is the usual aim.")
    z4, z5 = d.get("z4_low"), d.get("z5_low")
    if z4 and z5 and round(logic.EASY_CAP_OF_Z5 * z5) > 2 * z4 - z5:
        out += " Your easy limit reaches into zone 3, so part of your zone-3 time was easy running."
    return out


def _running_weeks(store: Store, today: date, weeks: int = 8) -> int:
    monday = today - timedelta(days=today.weekday())
    days = store.run_days(monday - timedelta(weeks=weeks), monday - timedelta(days=1))
    return len({d - timedelta(days=d.weekday()) for d in days})


def learned(store: Store, today: date, snap: dict | None = None) -> list[Fact]:
    """The facts as of `today`. `snap` is the snapshot when the caller already
    has one (the web server does); else it is assembled here."""
    from . import plan, snapshot

    snap = snap if snap is not None else snapshot.assemble(store, today=today)
    anchors = [today - timedelta(weeks=k) for k in range(CHANGE_WEEKS + 1)]
    habits_at = [plan.athlete_habits(store, a) for a in anchors]
    now = habits_at[0]
    facts: list[Fact] = []
    iso = today.isoformat()

    learned_week: list[Fact] = []
    if now.known:
        learned_week.append(Fact("days", "Running days", f"{now.days_per_week} a week",
                                 now.evidence["days"], "your history", iso,
                                 _change([h.days_per_week if h.known else None for h in habits_at])))
        if now.preferred:
            learned_week.append(Fact("weekdays", "Your days", ", ".join(now.preferred),
                                     now.evidence["preferred"], "your history", iso,
                                     _change([(", ".join(sorted(h.preferred)) or "no fixed weekdays")
                                              if h.known else None for h in habits_at])))
        elif "rhythm" in now.evidence:
            learned_week.append(Fact("weekdays", "Your days", "no fixed weekdays",
                                     now.evidence["rhythm"], "your history", iso))
        if now.long_run_day:
            learned_week.append(Fact("long_run", "Long run", now.long_run_day, now.evidence["long"],
                                     "your history", iso,
                                     _change([h.long_run_day if h.known else None
                                              for h in habits_at])))
        if now.avoid_hard:
            learned_week.append(Fact("other_sport", "Other sport", now.evidence["other"].removeprefix(
                "another sport on "), "no quality run that day or the day after",
                "your history", iso))
    else:
        learned_week.append(Fact("days", "Running days", "not known yet", now.evidence.get(
            "weeks", "too few weeks with a run"), "your history", iso))
    settings = profile_mod.load()
    facts += _with_settings(learned_week, habits.effective(settings, now), now, iso)

    if now.known and now.short_days:
        facts.append(Fact("short_days", "Your short days",
                          ", ".join(f"{d} up to {m} min" for d, m in now.short_days.items()),
                          now.evidence["short"] + " - the planner keeps every session there to it",
                          "your history", iso,
                          _change([", ".join(sorted(h.short_days)) or "none" if h.known else None
                                   for h in habits_at])))

    # No change line: 7 and 8 of 8 weeks are the same habit.
    facts.append(Fact("consistency", "Consistency",
                      f"{_running_weeks(store, today)} of the last 8 weeks with a run",
                      "Monday-weeks, the running week not counted", "your history", iso))

    dists = [store.get_intensity_distribution(a - timedelta(days=27), a) for a in anchors]
    d0 = dists[0]
    if d0.get("with_detail") and d0.get("easy_pct") is not None:
        facts.append(Fact(
            "intensity", "Intensity (28 days)",
            f"easy {round(d0['easy_pct'])} %, zone 3 {round(d0['moderate_pct'])} %, "
            f"hard {round(d0['hard_pct'])} % (zone 5 {round(d0['vo2max_pct'])} %)",
            f"{d0['with_detail']} of {d0['total_runs']} runs with zone detail; a polarised week is "
            f"~80 % easy", "your history", iso, _change([_grey_bucket(d) for d in dists]),
            _intensity_plain(d0)))

    facts += run_spread(store, today, iso)

    vo2 = snap.get("vo2max") or {}
    if vo2.get("current") is not None:
        trend = vo2.get("change_56d")
        meas = vo2.get("measurement") or {}
        value = f"{vo2['current']}" + (f" ({trend:+} in 8 weeks)" if trend is not None else "")
        evidence = (f"last new value from a run {vo2['last_change_days']} days ago"
                    if vo2.get("last_change_days") is not None else "no new value from a run on record")
        if meas.get("due"):
            evidence += "; a measurement is due"
        plain = "Garmin's estimate of your endurance - higher is fitter."
        if trend:
            plain += f" {'Up' if trend > 0 else 'Down'} {abs(trend)} in 8 weeks."
        if vo2.get("last_change_days") is None:
            plain += " Garmin updates it from runs; none in the last 8 weeks gave a new value."
        facts.append(Fact("vo2max", "VO2max", value, evidence, "Garmin", iso, plain=plain))

    balances = [store.latest_load_balance(a) for a in anchors]
    b0 = balances[0]
    if b0 and b0.get("load_balance"):
        facts.append(Fact("load_focus", "Garmin's load focus",
                          b0["load_balance"].lower().replace("_", " "),
                          f"Garmin's 4-week estimate as of {b0['day']} - its opinion, checked "
                          f"against the minutes above", "Garmin", str(b0["day"]),
                          _change([(b or {}).get("load_balance") for b in balances]),
                          "Garmin's own verdict on the mix of your last 4 weeks of training."))

    readiness = store.get_readiness(today) or {}
    sig = readiness.get("signals") or {}
    if sig.get("resting_hr_baseline") is not None:
        facts.append(Fact("resting_hr", "Resting HR baseline", f"{sig['resting_hr_baseline']} bpm",
                          "mean of the 27 nights before the last one", "Garmin", iso,
                          plain="Your usual resting heart rate. A morning value clearly above it "
                                "can mean you have not recovered yet."))
    zones = snap.get("zones") or {}
    if zones.get("max_hr_observed"):
        facts.append(Fact("max_hr", "Highest heart rate seen", f"{zones['max_hr_observed']} bpm",
                          f"on {zones.get('max_hr_day') or '?'}", "your history", iso,
                          plain="The highest heart rate your watch measured on a run."))
    if zones.get("source"):
        facts.append(Fact("zones", "Heart-rate zones", zones["source"],
                          f"zone 4 from {zones.get('z4_low')} bpm, zone 5 from "
                          f"{zones.get('z5_low')} bpm", "Garmin", str(zones.get("as_of_day") or iso),
                          plain=(f"From your watch: hard running starts at {zones.get('z4_low')} bpm "
                                 f"(zone 4), very hard at {zones.get('z5_low')} bpm (zone 5)."
                                 if zones.get("z4_low") and zones.get("z5_low") else None)))
    return facts


#: One word per kind of run, the run list's too: easy, moderate, hard.
_SHAPE_WORDS = (("easy", "easy"), ("middle", "moderate"), ("hard", "hard"))


def _counts(block: list[str]) -> str:
    return ", ".join(f"{n} {word}" for k, word in _SHAPE_WORDS if (n := block.count(k)))


def _mostly(block: list[str]) -> str:
    """The block in three words - what a change line compares. It reads SPREAD,
    and by count of runs: the app's own week (two hard of four runs) is 50/50
    or 40/60 depending on the weekday the block ends, and easy and hard apart
    every time - by time the hard part is a fifth. "Hardly an easy run" is
    under a third easy; "mixed" is easy and hard with more than a few moderate
    runs."""
    n = len(block)
    easy, middle, hard = block.count("easy"), block.count("middle"), block.count("hard")
    if middle > 0.5 * n:
        return "mostly moderate"
    if easy < 0.35 * n and hard >= 0.5 * n:
        return "mostly hard - hardly an easy run"
    if middle <= 0.3 * n:
        return "easy and hard kept apart" if hard else "mostly easy"
    return "mixed"


#: Each block's reading in plain words, and how the first runs are named.
_SPREAD_PLAIN = {
    "mostly moderate": ("Most runs were neither easy nor hard - the most common mistake: easy days "
                        "too fast, hard days not hard enough.", "mostly moderate"),
    "mostly hard - hardly an easy run": ("Almost every run was hard, with hardly an easy day to "
                                         "recover.", "almost all hard"),
    "easy and hard kept apart": ("Easy runs easy, hard runs hard - the way it should be.",
                                 "easy and hard kept apart"),
    "mostly easy": ("Almost every run was easy - hardly a hard session.", "almost all easy"),
    "mixed": ("", "mixed"),        # said from the kinds counted (`_mixed_words`)
}


def _mixed_words(block: list[str]) -> str:
    kinds = [w for k, w in _SHAPE_WORDS if k in block]
    return "A mix of " + (" and ".join(kinds) if len(kinds) < 3 else "easy, moderate and hard") + " runs."


def _day_words(iso_day: str) -> str:
    d = date.fromisoformat(str(iso_day)[:10])
    return f"{d.day} {d:%b}"


def _highest(maxes: list[int]) -> int | None:
    """The heart rate the runner reaches: the top tenth of the runs' maxima,
    never the single highest - a chest-strap or wrist spike or two decides
    nothing."""
    if len(maxes) < 3:
        return None
    ranked = sorted(maxes, reverse=True)
    return ranked[max(1, len(ranked) // 10)]


#: Zone 4 moved by this much between the first and the last runs: the change
#: line names it.
ZONE_SHIFT_BPM = 3


def _zone4(block: list[tuple[dict, str]]) -> int | None:
    """The zone-4 floor most of a block's runs were recorded with (the median)."""
    lows = sorted(r["hr_z4_low"] for r, _ in block if r.get("hr_z4_low"))
    return lows[len(lows) // 2] if lows else None


def run_spread(store: Store, today: date, iso: str) -> list[Fact]:
    """How each run was run (`logic.run_shape`), counted over the last
    SPREAD_BLOCK judged runs, with the earliest block of the window beside it
    when it reads differently - two blocks that never share a run - so the
    coach sees both the habit and its change, and the count moves with every
    synced run. Each run is judged by its own zone bounds; a run whose zone 4
    was set far too low (`logic.zones_plausible`) is left out, not judged.
    Recomputed on every read: a better rule changes the past too, and nothing
    the model wrote is kept."""
    start = today - timedelta(days=habits.LOOKBACK_WEEKS * 7 - 1)     # the sync's window
    runs = store.runs_with_zones(start, today)
    highest = _highest([r["max_hr"] for r in runs if r.get("max_hr")])
    judged: list[tuple[dict, str]] = []
    left_out = 0
    for r in runs:
        if logic.zones_plausible(r.get("hr_z4_low"), highest):
            left_out += 1
            continue
        shape = logic.run_shape(r, logic.interval_facts(r["splits"]))
        if shape:
            judged.append((r, shape["shape"]))
    off = (f"{left_out} runs left out - their zone 4 was set far below the heart rate you reach "
           "(set max heart rate or threshold in Garmin Connect for the runs to come; runs "
           "already recorded keep their zone times)") if left_out else ""
    # Runs whose zones the sync still fetches - 20 a sync, older ones at random.
    missing = store.count_runs(start, today, waiting=True)
    total = store.count_runs(start, today)
    if len(judged) < SPREAD_MIN:
        if not total:
            return []
        why = "; ".join(w for w in (
            f"the sync fetches zones and laps for {missing} more, 20 a sync" if missing else "",
            off,
            "" if missing or off else "more runs with enough zone time are needed - short runs "
                                      "and short reps are not judged") if w)
        plain = ("Not enough runs with heart-rate zones yet"
                 + (" - the app is still fetching them from Garmin, 20 per sync." if missing
                    else " - short runs and short intervals don't count." if not off else ".")
                 + (f" {left_out} runs are left out: their heart-rate zones look set too low."
                    if off else ""))
        return [Fact("spread", "How your runs went", "not judged yet",
                     f"{len(judged)} of {total} runs of the last {habits.LOOKBACK_WEEKS} weeks can be "
                     f"judged by their zone time; {why}", "your history", iso, plain=plain)]
    latest = [s for _, s in judged[:SPREAD_BLOCK]]
    earliest = judged[SPREAD_BLOCK:][-SPREAD_BLOCK:]
    first = [s for _, s in earliest]
    # Not while the older runs still come in at random: "your first runs" would
    # be whichever had arrived, and the line would come and go between syncs.
    # A few that never arrive (deleted on Garmin since) hold nothing back.
    held = missing * 10 > total and len(judged) + missing >= SPREAD_BLOCK + SPREAD_MIN
    evidence = ("each run by its zone time, as the run list colours it (hard also for 15 minutes in "
                "zones 4-5; a rep session by its reps; easy within the app's easy band); never one "
                "run alone" + (f"; {off}" if off else "")
                + (f"; your first runs are compared once the sync has most of the {missing} more "
                   f"run{'s' if missing != 1 else ''}" if held else ""))
    change = None
    if not held and len(first) >= SPREAD_MIN and _mostly(first) != _mostly(latest):
        change = f"your first runs from {earliest[-1][0]['local_day']}: {_mostly(first)} - {_counts(first)}"
        # Each run is read by the zones it was recorded with; when Garmin moved
        # them in between (its max-HR detection only goes up), part of this
        # change is the setting, not the running - said, not hidden.
        then, now = _zone4(earliest), _zone4(judged[:SPREAD_BLOCK])
        if then and now and abs(then - now) >= ZONE_SHIFT_BPM:
            change += (f"; zone 4 from {then} bpm then, {now} now - part of the change can be "
                       "the zones, not the running")
    reading = _SPREAD_PLAIN[_mostly(latest)][0] or _mixed_words(latest)
    plain = f"Your last {len(latest)} runs: {_counts(latest)}. {reading}"
    if change:
        plain += (f" Your first runs (from {_day_words(earliest[-1][0]['local_day'])}) were "
                  f"{_SPREAD_PLAIN[_mostly(first)][1]}.")
        if "part of the change can be the zones" in change:
            plain += (" Your heart-rate zones were set differently then, so part of that "
                      "difference is the setting.")
    if held:
        plain += " The comparison with your first runs comes once the app has fetched them."
    if off:
        plain += f" {left_out} runs are left out: their heart-rate zones look set too low."
    return [Fact("spread", "How your runs went", f"last {len(latest)}: {_mostly(latest)} - "
                 f"{_counts(latest)}", evidence, "your history", iso, change, plain)]


#: The week settings as facts: setting, fact key, label, how a value reads.
_SETTINGS = (
    ("days_per_week", "days", "Running days", lambda v: f"{v} a week"),
    ("run_days", "weekdays", "Your days", lambda v: ", ".join(v) or "no fixed weekdays"),
    ("long_run_day", "long_run", "Long run", str),
    ("off_days", "off_days", "Days you can't run", lambda v: ", ".join(v)),
    ("sport_days", "other_sport", "Other sport", lambda v: ", ".join(v) or "none"),
)


def _history_value(setting: str, h) -> object:
    """What the history alone says for a setting - for "your history says"."""
    if not h.known:
        return None
    return {"days_per_week": h.days_per_week, "long_run_day": h.long_run_day,
            "run_days": list(h.preferred) or None, "sport_days": list(h.sport_days) or None,
            "off_days": None}[setting]


def _with_settings(learned_week: list[Fact], eff: dict, h, iso: str) -> list[Fact]:
    """The week facts as the planner goes by them (`habits.effective`): what the
    athlete set, with the history named beside it where it differs; the
    history's own fact where it stands; and the history moved by a day off
    where a day off moved it - so the coach never reads "long run: sat" for a
    Saturday the athlete can no longer run on."""
    by_key = {f.key: f for f in learned_week}
    out: list[Fact] = []
    for setting, key, label, show in _SETTINGS:
        e = eff.get(setting)
        old = by_key.get(key)
        if e is None or (e.source != "you" and not e.adjusted):
            if old:
                out.append(old)
            continue
        if e.source == "you":
            hist = _history_value(setting, h)
            same = (sorted(hist) == sorted(e.value)) if isinstance(hist, list) else hist == e.value
            evidence = "set by you" + ("" if hist is None or same else f"; your history says {show(hist)}")
            out.append(Fact(key, label, show(e.value), evidence, "you", iso))
        else:
            out.append(Fact(key, label, show(e.value), e.evidence, "your history", iso))
    return out


def render_text(facts: list[Fact], cap: int) -> str:
    """The facts as lines for a prompt, cut at a whole fact: what does not fit
    is named, never half a sentence."""
    out, used = [], 0
    for i, f in enumerate(facts):
        line = f.line()
        if used + len(line) + 1 > cap:
            out.append(f"(+{len(facts) - i} more on the You tab)")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def fingerprint(facts: list[Fact]) -> str:
    """Changes when a fact's VALUE changes - the trigger of the coach's read."""
    return hashlib.sha1("\n".join(f"{f.key}={f.value}" for f in facts).encode()).hexdigest()[:16]


def week_settings(store: Store, today: date) -> dict:
    """What the You tab's editor needs: the values the athlete set, the ones
    the history would give (`None` where it cannot tell yet), and their note."""
    from . import plan

    h = plan.athlete_habits(store, today)
    known = h.known
    saved = profile_mod.load()
    return {
        "set": {k: v for k, v in saved.items() if k in profile_mod.WEEK_KEYS},
        "note": saved.get(profile_mod.NOTE_KEY) or "",
        "note_max": profile_mod.NOTE_MAX,
        # What they told the coach and it kept (roadmap 25), oldest first.
        "told": [{k: e.get(k) for k in ("id", "text", "day")} for e in profile_mod.told(saved)],
        "effective": {k: {"value": e.value, "source": e.source}
                      for k, e in habits.effective(saved, h).items()},
        "history": {
            "days_per_week": h.days_per_week if known else None,
            "run_days": [d for d in profile_mod.WEEKDAYS if d in h.preferred] if known else None,
            "long_run_day": h.long_run_day if known else None,
            "off_days": [],
            "sport_days": list(h.sport_days) if known else None,
        },
    }


_CACHE: dict = {}


def _stamp(path) -> float | None:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def cached(store: Store, today: date, snap: dict | None = None) -> list[Fact]:
    """`learned`, recomputed only when something it reads can have changed: the
    database (and its WAL), the profile, the account settings, or the day."""
    key = (store.path, _stamp(store.path), _stamp(store.path + "-wal"), today,
           _stamp(paths.profile_path()), _stamp(paths.account_path()))
    hit = _CACHE.get(store.path)
    if hit and hit[0] == key:
        return hit[1]
    result = learned(store, today, snap)
    _CACHE[store.path] = (key, result)
    return result
