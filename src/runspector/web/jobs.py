"""Coach jobs and cards as plain JSON files under `~/.runspector/`.

    jobs/j-YYYYMMDD-HHMMSS-<hex>.json   one job, status inside the file
    jobs/j-….log                        what the agent run printed
    cards/j-….json                      the resulting card (same id as its job)

Files rather than memory so that a restart loses nothing and a card can be
inspected with any editor. Writes are atomic (tmp + replace); the HTTP threads
read while the worker thread writes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from .. import evidence, paths

ACTIVE = ("queued", "running")
FINAL = ("done", "failed", "timeout", "cancelled")
log = logging.getLogger(__name__)

QUEUE_CAP = 3
RETENTION_DAYS = 14
RETENTION_MAX = 50
CARDS_MAX = 60

#: A job or card id. Always `fullmatch`: `match` with `$` lets a trailing
#: newline through. It is the path-traversal guard, so `_checked` applies it
#: inside every helper that turns an id into a file name.
ID_RE = re.compile(r"j-[0-9a-z-]+")


def _checked(job_id: str) -> str:
    if not isinstance(job_id, str) or not ID_RE.fullmatch(job_id):
        raise ValueError(f"not a job or card id: {job_id!r}")
    return job_id


def _now() -> datetime:
    return datetime.now().astimezone()


def new_id() -> str:
    for _ in range(5):
        jid = f"j-{_now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        if not job_path(jid).exists():
            return jid
    return f"j-{_now():%Y%m%d-%H%M%S}-{secrets.token_hex(4)}"


def job_path(job_id: str) -> Path:
    return paths.jobs_dir() / f"{_checked(job_id)}.json"


def log_path(job_id: str) -> Path:
    return paths.jobs_dir() / f"{_checked(job_id)}.log"


def card_path(card_id: str) -> Path:
    return paths.cards_dir() / f"{_checked(card_id)}.json"


def evidence_path(card_id: str) -> Path:
    """What the coach read for a card (`evidence.py`). It lives and dies with
    the card: `delete_card` and `cleanup_cards` remove both."""
    return paths.evidence_dir() / f"{_checked(card_id)}.json"


#: How long `_write_json` keeps trying to swap a file in. On Windows a reader
#: holding the target makes `os.replace` raise, and every page load reads these
#: files while the worker writes them. Six attempts with a linear backoff is
#: ~2 s, far longer than a JSON read of a few kilobytes.
_write_json = paths.write_json_atomic
_read_json = paths.read_json


# ── Jobs ─────────────────────────────────────────────────────────────────────

def new_job(prompt: str, *, title: str, kind: str, ctx: dict | None = None,
            parent: str | None = None, athlete_text: str | None = None,
            via: str | None = None) -> dict:
    """`athlete_text` is what the ATHLETE wrote for this job (a question, a
    follow-up) and nothing else - the one text a card may later propose to
    remember from. The prompt mixes it with model text and cannot serve."""
    job = {
        "id": new_id(), "title": title[:80], "prompt": prompt,
        "template_id": kind, "ctx": ctx or None, "parent": parent, "from_job": parent,
        "athlete_text": athlete_text, "via": via,
        "created": _now().isoformat(timespec="seconds"), "started": None, "finished": None,
        "status": "queued", "exit": None, "cost_usd": None, "result_summary": None, "note": None,
    }
    write_job(job)
    return job


def write_job(job: dict) -> None:
    _write_json(job_path(job["id"]), job)


def read_job(job_id: str) -> dict | None:
    return _read_json(job_path(job_id)) if ID_RE.fullmatch(job_id) else None


def read_jobs(limit: int = 20) -> list[dict]:
    """Newest first (the id carries the timestamp)."""
    files = sorted(paths.jobs_dir().glob("j-*.json"), reverse=True)
    return [j for j in (_read_json(p) for p in files[:limit])
            if j and isinstance(j.get("id"), str) and ID_RE.fullmatch(j["id"])]


def _cancel_path(job_id: str) -> Path:
    return paths.jobs_dir() / f"{_checked(job_id)}.cancel"


def request_cancel(job_id: str) -> None:
    """A marker FILE, so the HTTP thread never writes the job file the worker owns."""
    _cancel_path(job_id).write_text("", encoding="utf-8")


def cancel_requested(job_id: str) -> bool:
    return _cancel_path(job_id).exists()


def clear_cancel(job_id: str) -> None:
    _cancel_path(job_id).unlink(missing_ok=True)


def public(job: dict) -> dict:
    """What the UI gets: never the prompt, never the raw athlete text."""
    return {k: v for k, v in job.items() if k not in ("prompt", "athlete_text")}


def count_active() -> int:
    return sum(1 for j in read_jobs(100) if j.get("status") in ACTIVE)


def next_queued() -> dict | None:
    queued = [j for j in read_jobs(100) if j.get("status") == "queued"]
    return min(queued, key=lambda j: j["id"]) if queued else None


def recover_stale() -> int:
    """A job left `running` by a previous process never finishes on its own."""
    n = 0
    for j in read_jobs(100):
        if j.get("status") == "running":
            j.update(status="failed", finished=_now().isoformat(timespec="seconds"),
                     result_summary="interrupted by a restart")
            write_job(j)
            n += 1
    return n


def cleanup() -> None:
    finals = [j for j in read_jobs(1000) if j.get("status") in FINAL]
    cutoff = (_now() - timedelta(days=RETENTION_DAYS)).isoformat()
    for idx, job in enumerate(finals):
        if idx >= RETENTION_MAX or str(job.get("finished") or job.get("created") or "") < cutoff:
            # `.cancel` too: a job cancelled a moment before it finished on its
            # own leaves a marker that nothing ever removed, so `jobs/` collected
            # one file per such race, forever.
            for p in (job_path(job["id"]), log_path(job["id"]), _cancel_path(job["id"])):
                p.unlink(missing_ok=True)


# ── Cards ────────────────────────────────────────────────────────────────────

def card_key(c: dict) -> tuple:
    """Kind AND reference: two "analyze-run" cards for different runs are
    different things."""
    ctx = c.get("ctx")
    ref = "|".join(f"{k}={ctx[k]}" for k in sorted(ctx)) if isinstance(ctx, dict) and ctx else ""
    return (str(c.get("kind") or ""), ref)


def write_card(card_id: str, card: dict) -> None:
    _write_json(card_path(card_id), card)


def delete_card(card_id: str) -> bool:
    """Remove one card for good. This is not convenience: `cleanup_cards` pins the
    newest card of each kind forever, and `previous_card_block` feeds it into every
    later card of that kind — so a card steered by an untrusted workout name would
    otherwise keep steering, with no way out."""
    if not ID_RE.fullmatch(card_id) or not card_path(card_id).is_file():
        return False
    card_path(card_id).unlink(missing_ok=True)
    evidence_path(card_id).unlink(missing_ok=True)
    return True


def read_card(card_id: str) -> dict | None:
    return _read_json(card_path(card_id)) if ID_RE.fullmatch(card_id) else None


def write_evidence(card_id: str, doc: dict) -> None:
    _write_json(evidence_path(card_id), doc)


def read_evidence(card_id: str) -> dict | None:
    """A card's evidence - only while the card itself exists."""
    if not ID_RE.fullmatch(card_id) or not card_path(card_id).is_file():
        return None
    return _read_json(evidence_path(card_id))


def list_cards() -> list[tuple[str, dict]]:
    """Newest first by `generated_at` — NOT by mtime: giving feedback rewrites the
    file, which would make an old card jump to the top and become the "previous
    card" of the next run."""
    cards = [(p.stem, c) for p in paths.cards_dir().glob("j-*.json")
             if ID_RE.fullmatch(p.stem) and (c := _read_json(p)) is not None]
    cards.sort(key=lambda e: str(e[1].get("generated_at") or e[1].get("day") or ""), reverse=True)
    return cards


def latest_card(kind: str, ctx: dict | None = None, exclude: str | None = None) -> dict | None:
    for cid, c in list_cards():
        if cid != exclude and str(c.get("kind") or "") == kind and (c.get("ctx") or None) == (ctx or None):
            return {"id": cid, **c}
    return None


def cleanup_cards() -> None:
    """Keep the newest CARDS_MAX cards - plus always the newest card of each
    kind: it is the dedup anchor and the memory of the next run - and keep or
    drop a conversation WHOLE: dropping its card first left its answers loose,
    without the card they answer (roadmap 25)."""
    listed = list_cards()
    roots = _roots(listed)
    keep: set[str] = set()
    seen: set[tuple] = set()
    for idx, (cid, c) in enumerate(listed):
        first_of_kind = card_key(c) not in seen
        seen.add(card_key(c))
        if idx < CARDS_MAX or first_of_kind:
            keep.add(roots[cid])
    for cid, _ in listed:
        if roots[cid] not in keep:
            card_path(cid).unlink(missing_ok=True)
            evidence_path(cid).unlink(missing_ok=True)
    # A record whose card never got written (or went another way) goes too.
    # Safe here: this runs on the worker thread after `agent.run`, the one
    # writer of records, never between its record and its card.
    for p in paths.evidence_dir().glob("j-*.json"):
        if ID_RE.fullmatch(p.stem) and not card_path(p.stem).is_file():
            p.unlink(missing_ok=True)


def _cap(value, length: int) -> str:
    """Truncate VISIBLY. A silently cut headline turns a condition ("... if HRV
    recovers") into a claim."""
    s = str(value or "")
    return s if len(s) <= length else s[:length - 1].rstrip() + "…"


#: What `agent.NEXT_ACTIONS` allows - repeated here, since this module cannot
#: import the agent; `tests/test_web.py` pins the two together.
NEXT_ACTIONS = ("plan-week", "train-today", "week-review", "why-vo2max")


def _public_next(raw) -> list[dict] | None:
    out = [{"action": n["action"], "why": _cap(n.get("why"), 80)}
           for n in (raw if isinstance(raw, list) else [])
           if isinstance(n, dict) and n.get("action") in NEXT_ACTIONS][:2]
    return out or None


def _roots(listed: list[tuple[str, dict]]) -> dict[str, str]:
    """Each card's conversation root: up its `parent` chain to a card without
    one, or whose parent is no longer there (an older chain answer on answer)."""
    by_id = dict(listed)
    out: dict[str, str] = {}
    for cid, _ in listed:
        cur, seen = cid, set()
        while cur not in seen and len(seen) < 50:
            seen.add(cur)
            up = by_id.get(cur, {}).get("parent")
            if not isinstance(up, str) or up not in by_id:
                break
            cur = up
        out[cid] = cur
    return out


def conversation_root(card_id: str) -> str:
    """The card a conversation hangs on - the ONE answer the server, the card
    list and the cleanup ask (`_roots`): by cards only, so a deleted card is
    never climbed past through its job record."""
    return _roots(list_cards()).get(card_id, card_id)


def own_bullets(card: dict | None) -> list:
    """A card's points without those it marked [you]: what the athlete said -
    their note, a check-in, a kept fact - said back. A later card reads those
    from where they are kept now; carried on from an old card, a fact let go
    reached every card after it."""
    c = card or {}
    bullets = c.get("bullets") if isinstance(c.get("bullets"), list) else []
    sources = c.get("sources") if isinstance(c.get("sources"), list) else []
    return [b for i, b in enumerate(bullets)
            if not (i < len(sources) and isinstance(sources[i], dict) and sources[i].get("kind") == "you")]


def question_of(card: dict | None) -> str | None:
    """The athlete's question a card answers - only on a card this version
    wrote (`asked`, set by the app with it): an older one kept whatever a
    model put under `question`, and that is no word of the athlete's."""
    q = (card or {}).get("question")
    return q.strip() if (card or {}).get("asked") and isinstance(q, str) and q.strip() else None


def asked_day(card: dict | None) -> str:
    """The day a card was written, by the app's clock (`asked`) - not its
    `day`, which on a run's card is the run's date."""
    c = card or {}
    return str(c.get("asked") or c.get("generated_at") or c.get("day") or "?")[:10]


def running_in(root: str) -> int:
    """Answers on their way in a conversation: queued or running jobs on it."""
    return sum(1 for j in read_jobs(100) if j.get("status") in ACTIVE and j.get("parent") == root)


def scrub_told(entry: dict) -> None:
    """A kept fact the athlete let go: off the card that kept it and out of the
    records of what later runs were given. The conversation it came from stays
    until its card is deleted - letting a fact go is not deleting a question."""
    words = str(entry.get("text") or "")
    # Off every card that noted it (each version of it, by its id) - and those
    # are marked like the cards that were given it: their text may say it back.
    for cid, card in list_cards():
        told = card.get("told") if isinstance(card.get("told"), list) else []
        if any(isinstance(t, dict) and t.get("id") == entry.get("id") for t in told):
            card["told"] = [t for t in told if not (isinstance(t, dict) and t.get("id") == entry.get("id"))]
            if not card["told"]:
                card.pop("told")
            card["told_let_go"] = True
            _keep_writing(lambda cid=cid, card=card: card_path(cid).exists() and write_card(cid, card))
    if not words:
        return
    from .. import evidence, profile
    # Every line of this fact, by its id: it keeps its id when it changes or is
    # said again, so a record from before still holds it under an older day or
    # wording - and the athlete has no other way to reach that copy.
    prefix = profile.told_prefix(str(entry.get("id") or ""))

    def without(text):
        if isinstance(text, str) and any(line.startswith(prefix) for line in text.splitlines()):
            return "\n".join(line for line in text.splitlines() if not line.startswith(prefix)), True
        return text, False

    # Every stored text that held the line: what a card was given, and what a
    # tool returned to a card or a chat (`get_athlete_profile` lists the words).
    for p in paths.evidence_dir().glob("j-*.json"):
        rec = _read_json(p)
        if not isinstance(rec, dict):
            continue
        changed = False
        if isinstance(rec.get("inputs"), dict):
            rec["inputs"]["told"], c = without(rec["inputs"].get("told"))
            changed |= c
        for call in rec.get("calls") if isinstance(rec.get("calls"), list) else []:
            if isinstance(call, dict):
                call["result"], c = without(call.get("result"))
                changed |= c
        if changed:
            _keep_writing(lambda p=p, rec=rec: _write_json(p, rec))
            # The card that was given the fact may say it back anywhere - the
            # verdict too: as a previous card it is given by its day only.
            used = read_card(p.stem) if ID_RE.fullmatch(p.stem) else None
            if used is not None and not used.get("told_let_go"):
                used["told_let_go"] = True
                _keep_writing(lambda cid=p.stem, c=used: card_path(cid).exists() and write_card(cid, c))
    for p in evidence.chats_dir().glob("*.jsonl"):
        try:
            rows = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        out, changed = [], False
        for raw in rows:
            try:
                row = json.loads(raw)
            except ValueError:
                out.append(raw)
                continue
            if isinstance(row, dict):
                row["result"], c = without(row.get("result"))
                changed |= c
            out.append(json.dumps(row, ensure_ascii=False, default=str))
        if changed:
            # Whole or not at all: a reader (the page, the MCP process) never
            # sees half a file; a failed replace leaves no copy behind.
            tmp = p.with_name(p.name + ".tmp")

            def swap(p=p, tmp=tmp, out=out):
                try:
                    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
                    os.replace(tmp, p)
                finally:
                    tmp.unlink(missing_ok=True)
            _keep_writing(swap)


def _keep_writing(write) -> None:
    """One file a Forget cleans; one that cannot be written (a lock, a
    permission) is logged and the rest are still cleaned - a Forget is never
    stopped by one file, and the fact still goes."""
    try:
        write()
    except OSError:
        log.warning("could not clean a record of a fact let go", exc_info=True)


def public_cards(limit: int = 10) -> list[dict]:
    """Card content is model output: only the STRUCTURE is enforced and capped
    here; the frontend renders it escaped. A broken card file is skipped — it
    must not empty the list."""
    out = []
    # The newest `limit` CONVERSATIONS, each whole (roadmap 25): ten cards cut a
    # long conversation off its card, and left its answers without a field.
    listed = list_cards()
    roots = _roots(listed)
    keep: list[str] = []
    for cid, _ in listed:
        if roots[cid] not in keep:
            keep.append(roots[cid])
    keep_set = set(keep[:limit])
    # What each card kept, from where it is kept - a card's own field could be
    # older than the guard that writes it now.
    from .. import logic, profile
    kept: dict[str, list[dict]] = {}
    for e in profile.told():
        kept.setdefault(str(e.get("job") or ""), []).append(
            {"id": e["id"], "text": _cap(e["text"], 200),
             **({"instead": _cap(e["instead"], 200)} if isinstance(e.get("instead"), str) else {})})
    for cid, c in listed:
        if roots[cid] not in keep_set:
            continue
        bullets = c.get("bullets") if isinstance(c.get("bullets"), list) else []
        fb = c.get("feedback") if isinstance(c.get("feedback"), dict) else None
        out.append({
            "id": cid, "job_id": cid,
            "kind": _cap(c.get("kind"), 40), "ref": _cap(c.get("ref"), 40),
            "day": str(c.get("day") or "")[:10],
            # Caps for a broken card, not a normal one: 220 and 400 cut real cards
            # mid-word ("... it is the break fr…").
            "headline": _cap(c.get("headline"), 400),
            # The coach's "what to do": cut at 160 it stopped mid-word on the card.
            "verdict": _cap(c.get("verdict"), 600),
            "bullets": [_cap(b, 700) for b in bullets[:8]],
            "delta": _cap(c.get("delta"), 400),
            "text": "",
            "written": str(c.get("generated_at") or ""),
            "source": "job",
            "model": _cap(c.get("model"), 40),
            "generated_at": str(c.get("generated_at") or "")[:32],
            "data_through": str(c.get("data_through") or "")[:10],
            "snapshot_generated_at": str(c.get("snapshot_generated_at") or "")[:32],
            "cost_usd": c.get("cost_usd") if isinstance(c.get("cost_usd"), (int, float)) else None,
            "parent": c.get("parent") or None,
            "ctx": c.get("ctx") if isinstance(c.get("ctx"), dict) else None,
            # The id of a session the coach filed; `server.App._cards_with_proposals`
            # resolves it to preview + status (this module cannot import `plan`).
            "proposal": _cap(c.get("proposal"), 40) if isinstance(c.get("proposal"), str) else None,
            "feedback": ({"value": str(fb.get("value") or "")[:10], "text": _cap(fb.get("text"), 500),
                          "ts": str(fb.get("ts") or "")[:32]} if fb else None),
            "sources": ([_public_source(s) for s in c["sources"][:8]]
                        if isinstance(c.get("sources"), list) else None),
            "evidence": _public_evidence(c.get("evidence")),
            # Roadmap 25: the athlete's question an answer is for, the next steps
            # the card offers (checked against the app's menu again), the facts
            # it kept, and whether it was started as a next step.
            "question": _cap(question_of(c), 1000) or None,
            "next": _public_next(c.get("next")),
            "told": kept.get(cid) or None,
            # What it would have noted, had the list for the coach not been full.
            "told_full": c.get("told_full") is True,
            "via": "next" if c.get("via") == "next" else None,
            # A check-in the athlete can tap from the card - the app's own kinds
            # only, and only the day the card was written: a tap says it for
            # today. By `asked` - the app's day it was written - never `day`,
            # the model's, which is a run's date on a run's card.
            "check_in": (c.get("check_in") if c.get("check_in") in logic.CHECK_IN_KINDS
                         and c.get("asked") == paths.today().isoformat() else None),
        })
    return out


def _numbers_list(items, n: int) -> list[dict]:
    return [{"where": _cap(u.get("where"), 20), "value": _cap(u.get("value"), 20)}
            for u in (items if isinstance(items, list) else [])[:n] if isinstance(u, dict)]


def _public_source(s) -> dict | None:
    """Where one point comes from (`evidence.split_marks`, checked in `card_record`)."""
    if not isinstance(s, dict) or s.get("kind") not in ("measured", "you", "derived"):
        return None
    tool = s.get("tool") if isinstance(s.get("tool"), str) else None
    return {"kind": s["kind"], "ref": _cap(s.get("ref"), 60) or None,
            # The tool in the page's words: "today's readiness", not its name.
            "from": evidence.label(tool) if tool else None,
            "unmatched": [_cap(v, 20) for v in (s.get("unmatched") or [])[:6]
                          if isinstance(v, (str, int, float))],
            "no_data": s.get("no_data") is True,
            "called": s.get("called") if isinstance(s.get("called"), bool) else None}


def _public_evidence(e) -> dict | None:
    """The card's summary of its evidence; the full record is behind
    `/api/cards/<id>/evidence`."""
    if not isinstance(e, dict):
        return None

    def count(k: str) -> int:
        v = e.get(k)
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0

    unmatched = _numbers_list(e.get("unmatched"), 12)
    return {"recorded": e.get("recorded") is True, "complete": e.get("complete") is not False,
            "checked": e.get("checked") is not False,
            "reads": count("reads"), "numbers": count("numbers"), "in_data": count("in_data"),
            # every number found nowhere, also past the listed ones (older
            # cards carry only the list)
            "missing": count("missing") if "missing" in e else len(unmatched),
            "unmatched": unmatched}
