"""The athlete's own settings (`profile.json`): what they told the app, which
beats what the app read from their history.

The You tab shows the week the planner goes by - days, weekdays, long run,
days off, another sport - filled from the history (`habits.py`) and kept up
to date by it. Whatever the athlete sets here wins until they hand it back
("use my history"); `None` in a patch removes the key, and the history takes
over again. Next to the week, a free-text note to the coach. Everything is
validated here, once, for the web route and the CLI alike: the file is read
by the planner on every proposal, so a value it cannot use must never get in.
"""

from __future__ import annotations

import json
import os
import re
import threading

from . import paths

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
#: The week settings, as the You tab edits them.
WEEK_KEYS = ("days_per_week", "run_days", "long_run_day", "off_days", "sport_days")
#: Two running days at least must stay free: a long run and one more.
MAX_OFF_DAYS = 5
#: The athlete's note to the coach: free text, read by the coach (never by a
#: rule) before every answer - "gym three times a week", "less time lately".
NOTE_KEY = "note"
NOTE_MAX = 600
#: How the coach treats the athlete's note - shared by the card prompt
#: (`web/agent.py`) and `get_athlete_profile`, so a card and the chat read
#: the same note the same way.
NOTE_RULE = ("The athlete wrote this for their coach: take it into account - their time, other "
             "sports, what they want - wherever the answer has room for it, and say so when you "
             "do. It does not change today's decision, the spacing or the load rules, and it never "
             "stands in for their yes before anything goes on the watch.")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
#: What the athlete told the coach while asking on a card, kept for later
#: (roadmap 25): a fact in a few words (`text`, written by the coach and
#: checked against their message - `web/agent.py: kept_facts`), the day and
#: the card whose question it comes from (`job`; the You tab shows that
#: question from the card, so it goes when the card goes). Written only by a
#: card run; never through the settings route (`validate` refuses the key).
#: The You tab lets each go. TOLD_MAX full lines fit TOLD_CAP: every fact the
#: You tab lists reaches the coach. TOLD_SAID_MAX: how much of the question
#: the You tab shows.
TOLD_KEY = "told"
TOLD_MAX = 12
TOLD_FACT_MAX = 160
TOLD_SAID_MAX = 300
TOLD_CAP = 2800
TOLD_RULE = ("These are facts the athlete told the coach in earlier questions, each checked against "
             "their words when it was kept; the part in square brackets is its id. Take them into "
             "account like their note, and say so when you do; where two differ, the newer one "
             "counts. They do not change today's decision, the spacing or the load rules, and they "
             "never stand in for their yes before anything goes on the watch.")
#: One writer at a time: the worker keeps facts while the page saves the week,
#: the note or a Forget - each reads, changes and writes the whole file.
_LOCK = threading.RLock()
_ID_RE = re.compile(r"[0-9a-f]{8}")


def load() -> dict:
    try:
        data = json.loads(paths.profile_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _days(value, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(d, str) and d in WEEKDAYS for d in value):
        raise ValueError(f"{key} must be a list of weekdays ({', '.join(WEEKDAYS)})")
    return [d for d in WEEKDAYS if d in value]          # calendar order, no duplicates


def validate(patch: dict, current: dict) -> dict:
    """The profile after `patch`, or ValueError with a sentence for the page.
    Only the week keys are accepted; the other keys of `current` (max_hr,
    goal …) pass through untouched."""
    if not isinstance(patch, dict) or not patch:
        raise ValueError("nothing to save")
    unknown = sorted(set(patch) - set(WEEK_KEYS) - {NOTE_KEY})
    if unknown:
        raise ValueError(f"unknown setting: {', '.join(unknown)}")
    out = dict(current)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif key == NOTE_KEY:
            if not isinstance(value, str):
                raise ValueError("the note must be text")
            text = _CONTROL_RE.sub("", value.replace("\r\n", "\n")).strip()
            if len(text) > NOTE_MAX:
                raise ValueError(f"the note is too long (max {NOTE_MAX} characters)")
            if text:
                out[key] = text
            else:
                out.pop(key, None)
        elif key == "days_per_week":
            if isinstance(value, bool) or not isinstance(value, int) or not 2 <= value <= 7:
                raise ValueError("days_per_week must be a whole number from 2 to 7")
            out[key] = value
        elif key == "long_run_day":
            if value not in WEEKDAYS:
                raise ValueError(f"long_run_day must be one of {', '.join(WEEKDAYS)}")
            out[key] = value
        else:
            out[key] = _days(value, key)
    off = set(out.get("off_days") or ())
    if len(off) > MAX_OFF_DAYS:
        raise ValueError(f"at most {MAX_OFF_DAYS} days off - a week needs two running days")
    if out.get("long_run_day") in off:
        raise ValueError(f"the long run cannot be on a day off ({out['long_run_day']})")
    # A day off leaves the running days here, where it is saved - the page then
    # never has to rewrite a setting the athlete did not touch.
    if off and out.get("run_days"):
        out["run_days"] = [d for d in out["run_days"] if d not in off]
    if off and out.get("days_per_week", 0) > 7 - len(off):
        raise ValueError(f"{out['days_per_week']} runs a week do not fit next to {len(off)} "
                         f"days off - at most {7 - len(off)}")
    return out


def write(data: dict) -> None:
    """Atomically and owner-only - the note is the athlete's own words, and the
    web route and the CLI both write here."""
    path = paths.profile_path()
    paths.write_json_atomic(path, data)
    try:
        os.chmod(path, 0o600)
    except OSError:          # Windows: no POSIX modes, the home directory guards it
        pass


def save(patch: dict) -> dict:
    """Validate and write. Returns the new profile."""
    with _LOCK:
        new = validate(patch, load())
        write(new)
        return new


def _norm(text: str) -> str:
    return " ".join(re.findall(r"\w+", str(text or "").lower()))


def told(data: dict | None = None) -> list[dict]:
    """The kept facts, oldest first; anything that is not one is skipped."""
    raw = (load() if data is None else data).get(TOLD_KEY)
    return [e for e in raw if isinstance(e, dict) and isinstance(e.get("id"), str)
            and _ID_RE.fullmatch(e["id"]) and isinstance(e.get("text"), str)] \
        if isinstance(raw, list) else []


def keep_told(items: list[dict], *, job: str, day: str) -> list[dict]:
    """Add what a card run kept (`text`: the fact). A fact that changes one
    kept before (`replaces`: its id) takes its place, and so does the same fact
    said again - with the new day, at the end: the newest word counts, and an
    old copy never outranks it. Either way it keeps the old one's id, so one
    Forget reaches every line the coach was ever given of it
    (`web/jobs.scrub_told`). The newest TOLD_MAX stay. Returns the added ones,
    each with `instead` when it took the place of a different fact."""
    import secrets

    with _LOCK:
        data = load()
        current = told(data)
        added = []
        for item in items:
            text = str(item.get("text") or "").strip()[:TOLD_FACT_MAX]
            if not text or any(_norm(a["text"]) == _norm(text) for a in added):
                continue
            gone = [e for e in current
                    if e["id"] == item.get("replaces") or _norm(e["text"]) == _norm(text)]
            current = [e for e in current if e not in gone]
            entry = {"id": gone[0]["id"] if gone else secrets.token_hex(4),
                     "text": text, "day": day, "job": job}
            different = [g["text"] for g in gone if _norm(g["text"]) != _norm(text)]
            if different:
                entry["instead"] = different[0]
            current.append(entry)
            added.append(entry)
        if added:
            data[TOLD_KEY] = current[-TOLD_MAX:]
            write(data)
        return added


def forget_told(entry_id: str) -> dict | None:
    """Let one kept fact go; returns it (the caller scrubs it from the card
    and the records), None when there is no such fact."""
    if not isinstance(entry_id, str) or not _ID_RE.fullmatch(entry_id):
        return None
    with _LOCK:
        data = load()
        current = told(data)
        gone = next((e for e in current if e["id"] == entry_id), None)
        if gone is None:
            return None
        rest = [e for e in current if e["id"] != entry_id]
        if rest:
            data[TOLD_KEY] = rest
        else:
            data.pop(TOLD_KEY, None)
        write(data)
        return gone


def told_line(entry: dict) -> str:
    """One kept fact as the coach reads it - and as a record lists it. A Forget
    finds every line of it by its start, `told_prefix` (`web/jobs.scrub_told`):
    the id stays when the fact changes or is said again; the rest may not."""
    return f"- [{entry.get('id')}] {entry.get('text')} (said {entry.get('day') or '?'})"


def told_prefix(entry_id: str) -> str:
    return f"- [{entry_id}] "


def told_text(cap: int = TOLD_CAP) -> str:
    """The kept facts as lines for the coach, newest first within `cap` (which
    TOLD_MAX full lines fit - `tests/test_conversation.py` pins it)."""
    lines, used = [], 0
    for e in reversed(told()):
        line = told_line(e)
        if used + len(line) + 1 > cap:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)
