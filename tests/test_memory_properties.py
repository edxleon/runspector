"""What the coach keeps of the athlete's words, over long random courses
(roadmap 25): keeping, saying again, changing - also two changes at once -
records of what later cards and chats were given, letting go, deleting cards.
A shadow of what MUST be kept runs beside the real files, and after every step:

- never more than TOLD_MAX facts, each id and each wording once;
- nothing gone that was not let go or changed - a full list takes no new fact,
  it never pushes an old one out;
- every kept fact reaches the coach (`told_text`), as a card and a chat read it;
- a fact let go is in no record, no chat and on no card any more, under any of
  the days or wordings it had - its id is the thread through all of them -
  and every card that was given it or noted it is marked, so none says it
  back as a previous card;
- a card lists only what is kept now.

Deterministic: fixed seeds. `RUNSPECTOR_SIM_SEEDS=N` runs N more, the way the
simulator of the training rules does - the bug hunt, not the gate."""

from __future__ import annotations

import json
import os
import random

import pytest

from runspector import evidence, profile
from runspector.web import jobs, server

SEEDS = list(range(12)) + list(range(1000, 1000 + int(os.environ.get("RUNSPECTOR_SIM_SEEDS") or 0)))
STEPS = 140


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNSPECTOR_HOME", str(tmp_path))


class Course:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.n = 0
        self.alive: dict[str, str] = {}      # id -> text, what MUST be kept
        self.let_go: set[str] = set()
        self.cards: list[str] = []
        self.held: dict[str, set[str]] = {}  # card -> the fact ids it was given or noted
        self.app = server.App.__new__(server.App)
        self.app.store = None
        self.app.week = lambda: {}

    def card_id(self) -> str:
        self.n += 1
        return f"j-20261102-{self.n:06d}-{self.rng.randrange(16 ** 4):04x}"

    def new_text(self) -> str:
        return f"Fact {self.n} {self.rng.choice(['runs early', 'has a dog', 'likes trails', 'owns a strap'])}"

    # ── the steps ────────────────────────────────────────────────────────────

    def keep(self, items: list[dict]) -> None:
        """A card run that keeps `items`: its card, then what profile says."""
        cid = self.card_id()
        jobs.write_card(cid, {"kind": "ask", "headline": "h", "verdict": "v", "bullets": ["b"],
                              "question": "q", "asked": "2026-11-02"})
        before = dict(self.alive)
        added = profile.keep_told(items, job=cid, day=f"2026-11-{self.n % 28 + 1:02d}")
        self.cards.append(cid)
        if added:
            card = jobs.read_card(cid)
            card["told"] = [{"id": a["id"], "text": a["text"]} for a in added]
            jobs.write_card(cid, card)
        # The shadow, by the rules as stated: a change (`replaces`) or the same
        # words again takes the place of a fact kept BEFORE this card - each one
        # once - and keeps its id; anything else is new and needs room; nothing
        # else moves, and nothing is pushed out.
        expect, open_ = dict(before), dict(before)
        new_ids = iter(a["id"] for a in added if a["id"] not in before)
        for item in items:
            text = item["text"]
            if any(profile._norm(t) == profile._norm(text) for i, t in expect.items() if i not in open_):
                continue                                            # said twice in this card
            gone = [i for i, t in open_.items()
                    if i == item.get("replaces") or profile._norm(t) == profile._norm(text)]
            if not gone and len(expect) >= profile.TOLD_MAX:
                continue
            for i in gone:
                del open_[i]
                del expect[i]
            expect[gone[0] if gone else next(new_ids)] = text
        self.alive = expect
        self.held[cid] = {a["id"] for a in added}

    def record(self) -> None:
        """A later card and a chat that were given what is kept now."""
        cid = self.card_id()
        given = profile.told_text()
        jobs.write_card(cid, {"kind": "plan-week", "headline": "h", "verdict": "v", "bullets": ["b"]})
        call = {"tool": "get_athlete_profile", "result": f"Profile\n{given}\nend"}
        jobs.write_evidence(cid, {"inputs": {"told": given}, "calls": [call]})
        (evidence.chats_dir() / f"2026-11-{self.n % 28 + 1:02d}-{self.n}.jsonl").write_text(
            json.dumps({"tool": "get_athlete_profile", "result": f"x\n{given}\ny"}) + "\n", encoding="utf-8")
        self.cards.append(cid)
        self.held[cid] = set(self.alive)

    def forget(self) -> None:
        if not self.alive:
            return
        fid = self.rng.choice(sorted(self.alive))
        assert self.app.forget_fact({"id": fid})[1] == 200
        del self.alive[fid]
        self.let_go.add(fid)

    def delete_card(self) -> None:
        if self.cards:
            jobs.delete_card(self.cards.pop(self.rng.randrange(len(self.cards))))

    def step(self) -> str:
        r = self.rng.random()
        ids = sorted(self.alive)
        if r < 0.30:
            self.keep([{"text": self.new_text()}])
            return "new"
        if r < 0.40 and ids:
            fid = self.rng.choice(ids)
            self.keep([{"text": self.alive[fid].upper() + "!"}])          # the same words again
            return "again"
        if r < 0.52 and ids:
            self.keep([{"text": self.new_text(), "replaces": self.rng.choice(ids)}])
            return "change"
        if r < 0.58 and ids:
            fid = self.rng.choice(ids)                                    # two changes of one fact
            self.keep([{"text": self.new_text(), "replaces": fid},
                       {"text": self.new_text() + " too", "replaces": fid}])
            return "two changes"
        if r < 0.62:
            text = self.new_text()                                        # the same fact twice in one card
            self.keep([{"text": text}, {"text": text.lower() + "."}])
            return "twice in one card"
        if r < 0.78:
            self.record()
            return "record"
        if r < 0.92:
            self.forget()
            return "forget"
        self.delete_card()
        return "delete"

    # ── what must hold ───────────────────────────────────────────────────────

    def check(self, what: str) -> None:
        kept = profile.told()
        ids = [e["id"] for e in kept]
        assert len(kept) <= profile.TOLD_MAX, what
        assert len(set(ids)) == len(ids), f"{what}: an id twice"
        assert len({profile._norm(e['text']) for e in kept}) == len(kept), f"{what}: a fact twice"
        assert {e["id"]: e["text"] for e in kept} == self.alive, f"{what}: kept is not what must be"
        given = profile.told_text()
        assert all(profile.told_line(e) in given.splitlines() for e in kept), f"{what}: one misses the coach"
        for fid in self.let_go:
            prefix = profile.told_prefix(fid)
            for p in jobs.paths.evidence_dir().glob("j-*.json"):
                rec = json.loads(p.read_text(encoding="utf-8"))
                text = json.dumps(rec)
                assert prefix not in text, f"{what}: {fid} still in the record {p.name}"
            for p in evidence.chats_dir().glob("*.jsonl"):
                assert prefix not in p.read_text(encoding="utf-8"), f"{what}: {fid} still in {p.name}"
        for cid, held in self.held.items():
            card = jobs.read_card(cid)
            if card is None:
                continue
            stored = {t.get("id") for t in card.get("told") or []}
            assert not stored & self.let_go, f"{what}: card {cid} still stores a fact let go"
            if held & self.let_go:
                assert card.get("told_let_go") is True, f"{what}: card {cid} held a fact let go, unmarked"
        for c in jobs.public_cards(limit=500):
            for t in c.get("told") or []:
                assert t["id"] in self.alive and t["text"] == self.alive[t["id"]], \
                    f"{what}: card {c['id']} lists what is not kept now"


@pytest.mark.parametrize("seed", SEEDS)
def test_what_the_coach_keeps_holds_over_a_long_course(seed):
    course = Course(seed)
    for i in range(STEPS):
        what = course.step()
        course.check(f"seed {seed}, step {i} ({what})")


def test_the_course_reaches_every_kind_of_step_and_a_full_list(tmp_path, monkeypatch):
    """A course that never fills the list or never lets go would prove nothing
    about those."""
    seen, full = set(), False
    for seed in SEEDS[:4]:
        monkeypatch.setenv("RUNSPECTOR_HOME", str(tmp_path / str(seed)))
        course = Course(seed)
        for _ in range(STEPS):
            seen.add(course.step())
            full |= len(profile.told()) >= profile.TOLD_MAX
    assert seen >= {"new", "again", "change", "two changes", "twice in one card", "record", "forget",
                    "delete"}
    assert full, "no course reached a full list"
