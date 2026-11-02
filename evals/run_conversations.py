"""Conversations with the coach, end to end, with the real model (roadmap 25).

Every story in `conversations.yaml` runs against its own `runspector serve
--demo` (the synthetic athlete, a throw-away home) through the app's routes -
so what is measured is the app as it ships: the card frame, the conversation
the server builds, the next steps, the check before a fact is kept, Forget.
Each step is checked by code (what is kept, the check-in, what the coach was
given), then by a judge question where the answer itself matters.

    uv run --with pyyaml python evals/run_conversations.py [--story <id>] [--parallel 3]

Writes `CONVERSATIONS.md`, appends a row to `CONVERSATIONS_LOG.md`, exits 1 on
a failed step. A full run is about 18 card runs: 10-20 minutes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_evals  # noqa: E402 - the judge, shared with the coach evals

STORIES = HERE / "conversations.yaml"
RESULTS = HERE / "CONVERSATIONS.md"
LOG = HERE / "CONVERSATIONS_LOG.md"
#: Every answer of the last run with what was kept after it - what a red step
#: is read from. Untracked, like the coach evals' last-run.json.
LAST_RUN = HERE / "conversations-last-run.json"


class Server:
    """One `runspector serve --demo` in a throw-away home."""

    def __init__(self, home: Path):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.data = home / "demo"           # `--demo` keeps its data under <home>/demo
        env = {**os.environ, "RUNSPECTOR_HOME": str(home)}
        env.pop("RUNSPECTOR_CLAUDE_CMD", None)
        # Its own process group, so `stop` ends the server with everything under it.
        group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                 else {"start_new_session": True})
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "runspector.cli", "serve", "--demo", "--no-browser", "--no-sync",
             "--port", str(self.port)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **group)
        for _ in range(120):
            try:
                urllib.request.urlopen(self.base + "/", timeout=2)
                return
            except OSError:
                time.sleep(0.5)
        self.stop()
        raise RuntimeError("the demo server did not come up")

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Origin": self.base})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                code, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
        try:
            return code, json.loads(raw or b"{}")
        except ValueError:               # a proxy's or a crash's page: one failed step, not the run
            return code, {"error": raw[:200].decode("utf-8", "replace")}

    def read(self, *parts: str) -> dict | None:
        try:
            return json.loads((self.data.joinpath(*parts)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def wait(self, job_id: str, timeout: int) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            job = self.read("jobs", f"{job_id}.json") or {}
            if job.get("status") in ("done", "failed", "cancelled"):
                return job
            time.sleep(3)
        return {"status": "timeout"}

    def kept(self) -> list[dict]:
        return (self.read("profile.json") or {}).get("told", [])

    def active(self) -> list[str]:
        jobs = [self.read("jobs", p.name) or {} for p in (self.data / "jobs").glob("j-*.json")]
        return [j["id"] for j in jobs if j.get("status") in ("queued", "running") and j.get("id")]

    def stop(self) -> None:
        """Cancel what still runs through the app - its worker ends a card's
        `claude` and the fact check, which run in process groups of their own -
        then end the server and everything under it."""
        if self.proc.poll() is None:
            for jid in self.active():
                self.post(f"/api/jobs/{jid}/cancel", {})
            end = time.monotonic() + 30
            while self.active() and time.monotonic() < end:
                time.sleep(1)
        from runspector.web.agent import _kill_tree
        _kill_tree(self.proc)


def _answer(card: dict) -> str:
    return "\n".join([str(card.get("headline") or ""), str(card.get("verdict") or ""),
                      *[str(b) for b in card.get("bullets") or []]])


_WD = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _proposal_fails(srv: Server, card: dict, want: dict) -> list[str]:
    """The athlete's change the card filed (roadmap 19), against what they
    asked: `adds`/`takes_off` - weekdays that must get a session / lose an
    entry; `only` - and no other weekday is touched; `minutes` - weekday ->
    length of the session put there; `kinds` - weekday -> its kind;
    `within_days` - every day it touches lies this close to today
    ("Saturday" is this Saturday)."""
    import datetime as dt

    pid = card.get("proposal")
    p = srv.read("proposals", f"{pid}.json") if isinstance(pid, str) else None
    if not p:
        return [f"no proposal filed (card proposal: {pid!r})"]
    fails = [] if p.get("change") is True else ["the proposal is not a change to the week"]
    items = {_WD[dt.date.fromisoformat(it["day"]).weekday()]: it for it in p.get("items") or []}
    gone = {_WD[dt.date.fromisoformat(r["day"]).weekday()] for r in p.get("replaces") or []}
    fails += [f"no session put on {d} (put on: {sorted(items)})" for d in want.get("adds") or []
              if d not in items]
    fails += [f"nothing taken off {d} (taken off: {sorted(gone)})" for d in want.get("takes_off") or []
              if d not in gone]
    for d, m in (want.get("minutes") or {}).items():
        got = round(items[d]["spec"]["estimated_seconds"] / 60) if d in items else None
        if got is None or abs(got - int(m)) > 2:
            fails.append(f"{d} holds {got} min, asked {m}")
    for d, k in (want.get("kinds") or {}).items():
        if (items.get(d) or {}).get("spec", {}).get("kind") != k:
            fails.append(f"{d} is {(items.get(d) or {}).get('spec', {}).get('kind')}, asked {k}")
    if want.get("only"):
        named = set(want.get("adds") or []) | set(want.get("takes_off") or [])
        extra = (set(items) | gone) - named
        if extra:
            fails.append(f"it also changes {sorted(extra)}, which the athlete did not name")
    if "within_days" in want:
        today = dt.date.today()
        days = [dt.date.fromisoformat(x["day"]) for x in (p.get("items") or []) + (p.get("replaces") or [])]
        far = [str(d) for d in days if (d - today).days >= int(want["within_days"])]
        if far:
            fails.append(f"days beyond the next {want['within_days']}: {far}")
    return fails


def run_story(story: dict, defaults: dict) -> dict:
    timeout = int(defaults.get("timeout_sec", 600))
    out = {"id": story["id"], "steps": [], "failures": []}
    with tempfile.TemporaryDirectory(prefix="runspector-conv-") as tmp:
        srv = Server(Path(tmp))
        last = None
        try:
            for n, step in enumerate(story["steps"], 1):
                fails, note = [], ""
                card, job, secs = {}, {}, None
                if "forget" in step:
                    hit = [e for e in srv.kept() if re.search(step["forget"], e["text"], re.I)]
                    if not hit:
                        fails.append(f"nothing kept matches {step['forget']!r} to let go")
                    for e in hit:
                        code, _ = srv.post("/api/profile/forget", {"id": e["id"]})
                        if code != 200:
                            fails.append(f"Forget answered {code}")
                else:
                    body = ({"question": step["ask"]} if "ask" in step
                            else {"from_job": last, "prompt": step["follow"]} if "follow" in step
                            else {"template_id": step["template"], "force": True})
                    code, sp = srv.post("/api/spawn", body)
                    if code != 200:
                        fails.append(f"spawn answered {code}: {sp.get('error')}")
                    else:
                        jid = sp["job"]["id"]
                        if "while_pending" in step:
                            code2, sp2 = srv.post("/api/spawn", {"from_job": last or jid,
                                                                 "prompt": step["while_pending"]})
                            if code2 != 409 or sp2.get("code") != "answer_pending":
                                fails.append(f"a question while the answer was on its way got {code2} "
                                             f"{sp2.get('code')}, not 409 answer_pending")
                        job = srv.wait(jid, timeout)
                        card = srv.read("cards", f"{jid}.json") or {}
                        # How long the athlete waited for this card - the reason
                        # the coach's model was chosen (latency).
                        try:
                            secs = (datetime.fromisoformat(job["finished"])
                                    - datetime.fromisoformat(job["started"])).total_seconds()
                        except (KeyError, TypeError, ValueError):
                            secs = None
                        if job.get("status") != "done":
                            # The job's own words for why - `error` is no field of a job.
                            why = job.get("result_summary") or job.get("note") or job.get("error")
                            fails.append(f"the card run ended {job.get('status')}: {why}")
                        if "ask" in step:
                            last = jid
                        elif "follow" in step:
                            last = last or jid
                texts = [e["text"] for e in srv.kept()]
                for rx in step.get("memory_has") or []:
                    if not any(re.search(rx, t, re.I) for t in texts):
                        fails.append(f"nothing kept matches {rx!r} (kept: {texts})")
                for rx in step.get("memory_lacks") or []:
                    bad = [t for t in texts if re.search(rx, t, re.I)]
                    if bad:
                        fails.append(f"kept what must not be: {bad}")
                if "memory_max" in step and len(texts) > step["memory_max"]:
                    fails.append(f"kept {len(texts)} facts, at most {step['memory_max']}: {texts}")
                if "check_in" in step:
                    want = None if step["check_in"] in (None, "none") else step["check_in"]
                    if card.get("check_in") != want:
                        fails.append(f"check-in offered: {card.get('check_in')}, wanted {want}")
                if "proposal" in step:
                    fails += _proposal_fails(srv, card, step["proposal"])
                prompt = str((srv.read("jobs", f"{job.get('id')}.json") or {}).get("prompt") or "") \
                    if job.get("id") else ""
                for sub in step.get("prompt_has") or []:
                    if sub not in prompt:
                        fails.append(f"the coach's task lacks {sub!r}")
                given = str(((srv.read("evidence", f"{job.get('id')}.json") or {}).get("inputs") or {})
                            .get("told") or "")
                for rx in step.get("given_lacks") or []:
                    if re.search(rx, given, re.I):
                        fails.append(f"the coach was given {rx!r}: {given!r}")
                if step.get("judge") and card:
                    question = " ".join(step["judge"].split())
                    reply, _cost, err = run_evals.claude(
                        run_evals.JUDGE.format(question=question, answer=run_evals.neutralise(_answer(card))),
                        defaults.get("judge_model"), 300)
                    passed, why = (None, err) if err else run_evals.parse_verdict(reply)
                    note = why
                    if not passed:
                        fails.append(f"judge: {why}")
                out["steps"].append({"n": n, "ok": not fails, "fails": fails, "judge": note, "secs": secs,
                                     "answer": _answer(card) if card else "", "kept": texts,
                                     "check_in": card.get("check_in"), "next": card.get("next")})
                out["failures"] += [f"step {n}: {f}" for f in fails]
        finally:
            srv.stop()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--story")
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--note", default="")
    args = ap.parse_args()
    spec = yaml.safe_load(STORIES.read_text(encoding="utf-8"))
    stories = [s for s in spec["stories"] if args.story in (None, s["id"])]
    if not stories:
        print(f"no story {args.story!r}")
        return 2
    t0 = time.monotonic()
    with ThreadPoolExecutor(max(1, args.parallel)) as ex:
        results = list(ex.map(lambda s: run_story(s, spec.get("defaults") or {}), stories))
    LAST_RUN.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    steps = sum(len(r["steps"]) for r in results)
    ok = sum(1 for r in results for s in r["steps"] if s["ok"])
    for r in results:
        print(f"{'PASS' if not r['failures'] else 'FAIL'} {r['id']}")
        for f in r["failures"]:
            print(f"     {f}")
    minutes = (time.monotonic() - t0) / 60
    took = sorted(s["secs"] for r in results for s in r["steps"] if s.get("secs") is not None)
    median = took[len(took) // 2] if took else None
    print(f"{ok}/{steps} steps right, {len(results)} stories, {minutes:.0f} min"
          + (f", a card took {median:.0f} s (median), {took[-1]:.0f} s at most" if took else ""))
    model = os.environ.get("RUNSPECTOR_MODEL") or "sonnet (the app's default)"
    if args.story is None:
        rows = "\n".join(f"| {r['id']} | {'PASS' if not r['failures'] else 'FAIL'} | "
                         f"{'<br>'.join(r['failures']) or '-'} |" for r in results)
        RESULTS.write_text(
            f"# Conversations with the coach - last run\n\n"
            f"{datetime.now().astimezone():%Y-%m-%d %H:%M %Z}, coach model: {model}: "
            f"**{ok}/{steps} steps right**.\n\n| story | result | failures |\n|---|---|---|\n{rows}\n",
            encoding="utf-8")
        new = not LOG.exists()
        with LOG.open("a", encoding="utf-8") as log:
            if new:
                log.write("# Conversations with the coach - every full run\n\n"
                          "| when | coach model | right | failed stories | note |\n|---|---|---|---|---|\n")
            failed = "; ".join(r["id"] for r in results if r["failures"]) or "-"
            took_note = f" (a card {median:.0f} s median)" if median is not None else ""
            log.write(f"| {datetime.now().astimezone():%Y-%m-%d %H:%M} | {model} | {ok}/{steps} | "
                      f"{failed} | {args.note}{took_note} |\n")
    return 1 if any(r["failures"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
