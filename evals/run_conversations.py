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
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "runspector.cli", "serve", "--demo", "--no-browser", "--no-sync",
             "--port", str(self.port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(120):
            try:
                urllib.request.urlopen(self.base + "/", timeout=2)
                return
            except OSError:
                time.sleep(0.5)
        raise RuntimeError("the demo server did not come up")

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Origin": self.base})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

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

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _answer(card: dict) -> str:
    return "\n".join([str(card.get("headline") or ""), str(card.get("verdict") or ""),
                      *[str(b) for b in card.get("bullets") or []]])


def run_story(story: dict, defaults: dict) -> dict:
    timeout = int(defaults.get("timeout_sec", 600))
    out = {"id": story["id"], "steps": [], "failures": []}
    with tempfile.TemporaryDirectory(prefix="runspector-conv-") as tmp:
        srv = Server(Path(tmp))
        last = None
        try:
            for n, step in enumerate(story["steps"], 1):
                fails, note = [], ""
                card, job = {}, {}
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
                        if job.get("status") != "done":
                            fails.append(f"the card run ended {job.get('status')}: {job.get('error')}")
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
                out["steps"].append({"n": n, "ok": not fails, "fails": fails, "judge": note,
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
    print(f"{ok}/{steps} steps right, {len(results)} stories, {minutes:.0f} min")
    model = os.environ.get("RUNSPECTOR_MODEL") or "the CLI's default"
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
            log.write(f"| {datetime.now().astimezone():%Y-%m-%d %H:%M} | {model} | {ok}/{steps} | "
                      f"{failed} | {args.note} |\n")
    return 1 if any(r["failures"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
