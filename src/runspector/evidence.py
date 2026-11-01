"""What the AI read, kept so that a reader can check what it says.

Two records, both on this machine only, under `~/.runspector/evidence/`:

* **A coach card's evidence** (`<card id>.json`, written by `web/agent.py`):
  every tool the card run called, with what the tool returned, and what the
  run was given. The CLI prints its whole session as a stream of JSON events
  (`--output-format stream-json`); `parse_stream` keeps it. It lives and dies
  with its card (`web/jobs.py`).
* **The chat record** (`chats/<day>-<pid>.jsonl`, written by `mcp_server.py`):
  every tool call a chat in Claude Code or Claude Desktop makes, kept
  `CHAT_DAYS` days. One file per server process, so two chats never write
  into the same file.

`check` then looks for every number of a card in what the coach read. The
check is literal, and says so on the card: a number found proves it was in
front of the coach, not that it is used rightly; a number not found was
worked out by the coach or is not backed by the data. It reports, it never
rejects a card.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

#: What each tool reads or does, in the words the page shows. `tests/test_tools.py`
#: pins that every tool the MCP server registers has one.
TOOL_LABELS = {
    "get_coaching_reference": "the coaching rules",
    "get_athlete_profile": "what the app knows about you",
    "get_training_readiness": "today's readiness",
    "get_recovery_summary": "your recovery (HRV, sleep, resting heart rate)",
    "get_daily_metrics": "one day's numbers",
    "get_trend": "a trend over time",
    "get_training_load": "your training load",
    "get_recent_activities": "your recent runs",
    "get_intensity_distribution": "your time in heart-rate zones",
    "analyze_workout": "one run in detail",
    "get_vo2max_history": "your VO2max history",
    "sync_garmin": "a sync with Garmin",
    "propose_workout": "a draft session (not on the watch)",
    "propose_week": "a draft week (not on the watch)",
    "apply_workout": "a session put on your watch",
    "undo_workout": "a session taken off your watch",
    "check_in": "what you said about how you are",
}

#: The tools that change something rather than read. The page says "did",
#: not "read", for these.
CHANGING_TOOLS = frozenset({"sync_garmin", "propose_workout", "propose_week",
                            "apply_workout", "undo_workout", "check_in"})

#: Per tool result, what the record keeps. A week's analysis is a few KB; the
#: cap only bounds a runaway answer. The number check runs on the full text.
RESULT_CAP = 20_000
CHAT_DAYS = 14
_PREFIX = "mcp__runspector__"


def tool_name(name: str) -> str:
    """`mcp__runspector__get_trend` (the CLI's name) -> `get_trend`."""
    name = str(name or "")
    return name[len(_PREFIX):] if name.startswith(_PREFIX) else name


def label(name: str) -> str:
    n = tool_name(name)
    return TOOL_LABELS.get(n, n.replace("_", " "))


def _cap(text: str, limit: int = RESULT_CAP) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… ({len(text) - limit} more characters)"


def _unwrap(text: str) -> str:
    """The CLI hands an MCP tool's answer on in its structured form,
    `{"result": "<the text>"}`. The text inside is what the tool said - and
    what the athlete should see under "What came back", not escaped JSON.
    Anything else that looks like JSON stays as it came."""
    if not text.startswith('{"result"'):
        return text
    try:
        obj = json.loads(text)
    except ValueError:
        return text
    if isinstance(obj, dict) and set(obj) == {"result"} and isinstance(obj["result"], str):
        return obj["result"]
    return text


def _text(content) -> str:
    """A tool result's content: a string, or a list of blocks."""
    if isinstance(content, str):
        return _unwrap(content)
    if isinstance(content, list):
        return "\n".join(_unwrap(str(b.get("text") or "")) if isinstance(b, dict) and b.get("type") == "text"
                         else f"[{b.get('type', 'block')}]" if isinstance(b, dict) else str(b)
                         for b in content)
    return "" if content is None else str(content)


# ── a card run's session ─────────────────────────────────────────────────────

def parse_stream(out: str) -> dict:
    """The CLI's stdout -> `{"envelope", "calls", "complete", "stream"}`.

    `envelope` is the final result event - the same object `--output-format
    json` prints alone, which is accepted too (then no calls are known and
    `stream` is False). `calls` are the tool calls in order, each with what
    came back. `complete` is False when lines were dropped or unreadable, so
    the record never claims to be whole when it is not."""
    text = (out or "").strip()
    try:
        whole = json.loads(text)
    except ValueError:
        whole = None
    if isinstance(whole, dict):
        return {"envelope": whole, "calls": [], "complete": True, "stream": False}
    calls: list[dict] = []
    by_id: dict[str, dict] = {}
    envelope, complete = None, True
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            complete = False          # the tail cap's "[... dropped ...]" line, or a cut one
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "result":
            envelope = ev
            continue
        if ev.get("type") not in ("assistant", "user"):
            continue
        content = (ev.get("message") or {}).get("content") if isinstance(ev.get("message"), dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                call = {"id": str(block.get("id") or ""), "tool": tool_name(block.get("name")),
                        "input": block.get("input") if isinstance(block.get("input"), dict) else {},
                        "result": None, "error": False}
                calls.append(call)
                by_id[call["id"]] = call
            elif block.get("type") == "tool_result":
                call = by_id.get(str(block.get("tool_use_id") or ""))
                if call is not None:
                    call["result"] = _text(block.get("content"))
                    call["error"] = bool(block.get("is_error"))
    return {"envelope": envelope, "calls": calls, "complete": complete, "stream": True}


def whole(stream: dict) -> bool:
    """Is the record the whole session? Only then may it doubt a point: a cut
    record (the output cap) or no session at all (a one-object answer) would
    turn the record's gap into the coach's. Every check asks this."""
    return bool(stream.get("stream") and stream.get("complete"))


def own_words(out: str) -> str:
    """The run's output without anything the tools said: what may be kept or
    read outside the card's record - the job log, the failure reason, the quota
    check. Drops the session's messages (tool calls, their results, the model's
    turns), a half event, and the line the output cap cut."""
    kept, after_cut = [], False
    for line in (out or "").splitlines():
        cut, after_cut = after_cut, line.startswith("[... ") and "dropped" in line
        if cut:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            if not line.lstrip().startswith("{"):
                kept.append(line)
            continue
        if not (isinstance(ev, dict) and ev.get("type") in ("user", "assistant")):
            kept.append(line)
    return "\n".join(kept)


# ── where a point comes from ─────────────────────────────────────────────────

SOURCE_KINDS = ("measured", "you", "derived")
_MARK = re.compile(r"^\s*\[\s*(measured|you|derived)\s*(?:[:\-–—]\s*([^\]]{0,160}))?\]\s*", re.I)
_TOOL_IN_REF = re.compile(r"\b(get_[a-z0-9_]+|analyze_workout|sync_garmin|propose_week|"
                          r"propose_workout|check_in)\b")


def split_marks(bullets: list) -> tuple[list, list]:
    """`["[measured: get_training_readiness] HRV 38 ms", "plain"]` ->
    (`["HRV 38 ms", "plain"]`, `[{"kind": "measured", "ref": ..., "tool": ...}, None]`).

    The mark leaves the text, so every other reader of a card - the previous
    card block, an old page - sees the point as before. A bullet without a
    known mark keeps its text and gets None."""
    texts, sources = [], []
    for b in bullets:
        m = _MARK.match(b) if isinstance(b, str) else None
        if not m:
            texts.append(b)
            sources.append(None)
            continue
        ref = (m.group(2) or "").strip() or None
        # The CLI's full name (`mcp__runspector__get_trend`) names the tool too.
        tool = _TOOL_IN_REF.search((ref or "").replace(_PREFIX, " "))
        texts.append(b[m.end():])
        sources.append({"kind": m.group(1).lower(), "ref": ref,
                        "tool": tool.group(1) if tool else None})
    return texts, sources


# ── the number check ─────────────────────────────────────────────────────────

# A date, a time or pace (4:35, 1:02:10), or a plain number. A number glued
# to a letter on its left ("VO2max", "Z5", "x4") is a label, not a value. Each
# kind has its own end: the tools print "- 2026-10-16: 49.4", so a colon may
# follow a date, but not a number that is the start of a time.
_NUM = re.compile(r"(?<![A-Za-z\d.:])(?:\d{4}-\d{2}-\d{2}(?!\d)"
                  r"|\d+:\d{2}(?::\d{2})?(?![\d:])"
                  r"|\d+(?:\.\d+)?(?!\d|:\d))")


def numbers(text: str) -> list[str]:
    # A "number" longer than any value a tool prints is noise - and a time of
    # 5,000 digits would not even convert.
    return [t for t in _NUM.findall(str(text or "")) if len(t) <= 24]


def _key(tok: str):
    if "-" in tok:
        return ("date", tok)
    if ":" in tok:
        parts = [int(p) for p in tok.split(":")]
        secs = 0
        for p in parts:
            secs = secs * 60 + p
        return ("time", secs)
    return ("num", float(tok))


#: How the tools print a duration: "7h 13m", "1h 09m", "4m 35s".
_HM = re.compile(r"\b(\d+)h\s?(\d{1,2})m\b")
_MS = re.compile(r"\b(\d+)m\s?(\d{1,2})s\b")


class _Corpus:
    """The numbers of one text, for `found` - also in the everyday forms a
    card writes them in: a day the tools print as 2026-11-17 is "17 Nov" on a
    card, a duration printed 7h 13m is "7:13"."""

    def __init__(self, text: str):
        self.dates, self.times, self.nums = set(), set(), set()
        for tok in numbers(text):
            kind, v = _key(tok)
            {"date": self.dates, "time": self.times, "num": self.nums}[kind].add(v)
            if kind == "date":
                year, month, day = (int(x) for x in v.split("-"))
                self.nums.update({float(year), float(month), float(day)})
        for pattern in (_HM, _MS):
            for a, b in pattern.findall(str(text or "")):
                self.times.add(int(a) * 60 + int(b))

    def has(self, tok: str) -> bool:
        kind, v = _key(tok)
        if kind == "date":
            return v in self.dates
        if kind == "time":
            return v in self.times
        # 47.9 in the data backs a "48" on the card: the card rounds to its
        # own precision, so a value within half a unit of it is the same number
        # - 56.5 backs a "57" (Python's `round` would say 56). The other way
        # round does not hold.
        decimals = len(tok.split(".")[1]) if "." in tok else 0
        half = 0.5 * 10 ** -decimals + 1e-9
        return any(abs(s - v) <= half for s in self.nums)


def check(card: dict, *, data: str, context: str = "", rules: str = "",
          sources: list | None = None) -> dict:
    """Where each number of the card's text can be found: in what the tools
    returned (`data`), in what the run was given about the athlete and the
    task (`context`), or in the coaching rules (`rules`).

    Returns the summary the card carries: how many numbers, how many in the
    data, the ones found nowhere (with where on the card), and per point the
    numbers found nowhere and whether a point marked as measured holds a
    number from the data at all."""
    corpora = [("data", _Corpus(data)), ("context", _Corpus(context)), ("rules", _Corpus(rules))]

    def where_found(tok: str) -> str | None:
        return next((name for name, c in corpora if c.has(tok)), None)

    fields = [("headline", card.get("headline")), ("verdict", card.get("verdict")),
              ("since last card", card.get("delta"))]
    bullets = card.get("bullets") if isinstance(card.get("bullets"), list) else []
    total = in_data = 0
    unmatched: list[dict] = []
    for where, text in fields:
        for tok in numbers(text):
            total += 1
            found = where_found(tok)
            in_data += found == "data"
            if found is None:
                unmatched.append({"where": where, "value": tok})
    points = []
    for i, text in enumerate(bullets):
        src = sources[i] if sources and i < len(sources) else None
        toks = numbers(text) if isinstance(text, str) else []
        found = [where_found(t) for t in toks]
        total += len(toks)
        in_data += sum(f == "data" for f in found)
        missing = [t for t, f in zip(toks, found, strict=True) if f is None]
        unmatched += [{"where": f"point {i + 1}", "value": t} for t in missing]
        measured = bool(src and src.get("kind") == "measured")
        points.append({"unmatched": missing,
                       "no_data": measured and bool(toks) and "data" not in found})
    return {"numbers": total, "in_data": in_data, "unmatched": unmatched, "points": points}


def card_record(*, job_id: str, model: str, stream: dict, card: dict, sources: list,
                inputs: dict, data_rules: str, data_context: str,
                given: dict[str, str] | None = None,
                given_rules: tuple[str, ...] = ()) -> tuple[dict, dict, list]:
    """(the evidence file, the summary the card carries, the sources with their
    check). The number check runs on the full tool results; the file keeps
    them capped. `given` is the athlete's data a tool would have returned that
    the run had in its prompt already ({tool: text}): looked at, and data.
    `given_rules` names tools whose text (the rules) the prompt held: looked
    at, but never data - a rule's number is not the athlete's."""
    calls = stream["calls"]
    given = {k: v for k, v in (given or {}).items() if v}
    data = "\n".join([*(c["result"] or "" for c in calls), *given.values()])
    result = check(card, data=data, context=data_context, rules=data_rules, sources=sources)
    if not whole(stream):
        # Nothing doubted on a record that is not the whole session (`whole`).
        result["unmatched"] = []
        result["points"] = [{"unmatched": [], "no_data": False} for _ in result["points"]]
    called = {c["tool"] for c in calls} | set(given) | set(given_rules)
    checked = []
    for src, point in zip(sources, result["points"], strict=True):
        if src is None:
            checked.append(None)
            continue
        checked.append({**src, "unmatched": point["unmatched"], "no_data": point["no_data"],
                        "called": (src["tool"] in called) if src.get("tool") and whole(stream) else None})
    # `missing` counts every number found nowhere; `unmatched` lists the first.
    summary = {"recorded": stream["stream"], "complete": stream["complete"], "checked": whole(stream),
               "reads": len(calls), "numbers": result["numbers"], "in_data": result["in_data"],
               "missing": len(result["unmatched"]), "unmatched": result["unmatched"][:12]}
    doc = {"version": 1, "job_id": job_id, "model": model,
           "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "complete": stream["complete"], "recorded": stream["stream"], "inputs": inputs,
           "calls": [{"tool": c["tool"], "input": c["input"], "error": c["error"],
                      "chars": len(c["result"] or ""), "result": _cap(c["result"] or "")}
                     for c in calls],
           "check": {"checked": whole(stream), "numbers": result["numbers"], "in_data": result["in_data"],
                     "missing": len(result["unmatched"]), "unmatched": result["unmatched"]}}
    return doc, summary, checked


# ── the chat record ──────────────────────────────────────────────────────────

def chats_dir() -> Path:
    d = paths.evidence_dir() / "chats"
    d.mkdir(exist_ok=True)
    return d


def _result_text(result) -> str:
    """What a FastMCP tool call returned, as text: a list of content blocks,
    a (blocks, structured) pair, or a plain value."""
    if isinstance(result, tuple) and result:
        result = result[0]
    if isinstance(result, (list, tuple)):
        return "\n".join(str(getattr(b, "text", None) or b) for b in result)
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    return str(result)


#: The day this process last pruned the chat record: a Claude Desktop keeps one
#: server running for weeks, so pruning only at its start kept every day.
_pruned_on: str | None = None
ARGS_CAP = 2_000


def record_chat_call(tool: str, args: dict | None, *, ok: bool, ms: int, result) -> None:
    """One line in today's file of this process. Never raises: the record is
    for the reader, and a full disk must not cost the athlete the answer."""
    global _pruned_on
    try:
        today = f"{datetime.now():%Y-%m-%d}"
        if _pruned_on != today:
            _pruned_on = today
            prune_chats()
        text = _result_text(result)
        args = args if isinstance(args, dict) else {}
        dumped = json.dumps(args, ensure_ascii=False, default=str)
        if len(dumped) > ARGS_CAP:
            args = {"(cut)": dumped[:ARGS_CAP]}
        line = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                "tool": tool_name(tool), "args": args,
                "ok": ok, "ms": ms, "chars": len(text), "result": _cap(text)}
        path = chats_dir() / f"{datetime.now():%Y-%m-%d}-{os.getpid()}.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    except Exception:  # the record is extra - see above
        log.warning("could not record the chat's tool call %s", tool, exc_info=True)


def _first_kept_day() -> str:
    """Today and the CHAT_DAYS - 1 days before it: CHAT_DAYS days, as the page says."""
    return f"{datetime.now() - timedelta(days=CHAT_DAYS - 1):%Y-%m-%d}"


def prune_chats() -> None:
    """Drop the chat record's files older than CHAT_DAYS (by the day in the name)."""
    try:
        cutoff = _first_kept_day()
        for p in chats_dir().glob("*.jsonl"):
            if p.name[:10] < cutoff:
                p.unlink(missing_ok=True)
    except OSError:
        log.warning("could not prune the chat record", exc_info=True)


def _detail(args: dict) -> str:
    """The arguments as a short phrase: "hrv avg ms, 28 days"."""
    parts = []
    for k, v in list((args or {}).items())[:3]:
        if v in (None, "", [], {}):
            continue
        value = str(v)[:30].replace("_", " ")
        if k in ("metric", "kind"):
            parts.append(value)
        elif k == "days" or k.endswith("_days"):
            parts.append(f"{value} days")
        else:
            parts.append(f"{k.replace('_', ' ')} {value}")
    return ", ".join(parts)


def recent_chat_calls(limit: int = 20) -> list[dict]:
    """The newest chat tool calls, for the page: when, what, whether it
    worked - not what came back (that stays in the files)."""
    rows: list[dict] = []
    cutoff = _first_kept_day()
    try:
        by_day: dict[str, list[Path]] = {}
        for p in chats_dir().glob("*.jsonl"):
            # A file the pruning has not reached yet is still not shown: the
            # page promises CHAT_DAYS days, whoever pruned last.
            if p.name[:10] >= cutoff:
                by_day.setdefault(p.name[:10], []).append(p)
        # Newest day first, and stop once a whole day has filled the list: the
        # files carry what came back and grow, the page needs only the top.
        for day in sorted(by_day, reverse=True):
            for p in by_day[day]:
                for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        r = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and isinstance(r.get("tool"), str):
                        rows.append(r)
            if len(rows) >= limit:
                break
    except OSError:
        return []
    rows.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    return [{"ts": str(r.get("ts") or "")[:32], "tool": r["tool"][:60], "label": label(r["tool"]),
             "changed": r["tool"] in CHANGING_TOOLS, "ok": bool(r.get("ok")),
             "detail": _detail(r.get("args") if isinstance(r.get("args"), dict) else {})}
            for r in rows[:limit]]
