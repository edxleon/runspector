"""The check before the coach keeps a fact, with the real model (roadmap 25).

`web/agent.py: verify_facts` decides which notes the coach may keep of what the
athlete wrote. It is a prompt to a model, so it gets regression cases like
the coach prompts do: `memory_check.yaml`, each note with the verdict the check
must give, each case run `--repeat` times - one wrong verdict in any repeat is a
miss. The prompt and the model are the app's own, imported, never copied.

    uv run --with pyyaml python evals/run_memory_check.py [--repeat 3] [--case <id>] [--no-rules]

`--no-rules`: the negative control - the same cases against a prompt that only
asks whether the message says the note. The cases that still pass there are not
carried by the check's rules; the summary lists them, and the exit code is 0.
Writes `MEMORY_CHECK.md`; exits 1 on a miss.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import yaml

from runspector.web import agent

HERE = Path(__file__).resolve().parent
CASES = HERE / "memory_check.yaml"
RESULTS = HERE / "MEMORY_CHECK.md"
#: Every full run, one row each, kept: which model and which prompt got how
#: many verdicts right - the record a model or prompt change is decided on.
LOG = HERE / "MEMORY_CHECK_LOG.md"

#: The negative control: the frame of the shipped prompt with its rules gone.
BARE = """You check notes a running app wants to keep about an athlete, against their message below.

{earlier}--- MESSAGE {nonce} ---
{message}
--- END MESSAGE {nonce} ---
--- NOTES {nonce} ---
{notes}
--- END NOTES {nonce} ---

Answer with exactly one JSON object and nothing else. For each note, its number as the key and
as the value the words of the message that decide it, then your verdict:
{{"1": {{"why": "<the deciding words>", "keep": true or false}}, "2": ...}} - one key for every note."""


def load(case_id: str | None = None) -> list[dict]:
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))["cases"]
    return [c for c in cases if case_id in (None, c["id"])]


def run_case(case: dict) -> list[bool]:
    """The way the app keeps a fact: its cheap gates first (`candidate_facts` -
    on the fact itself, without the kept note a change names), then the check
    on the notes that pass. A note the gates drop is refused."""
    passing = [n["note"] for n in case["notes"]
               if agent.candidate_facts([{"fact": n["note"].split(" (changes the kept note:")[0]}],
                                        case["message"])]
    checked = dict(zip(passing, agent.verify_facts(passing, case["message"], case.get("earlier", "")),
                       strict=True)) if passing else {}
    return [checked.get(n["note"], False) for n in case["notes"]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--case")
    ap.add_argument("--no-rules", action="store_true")
    ap.add_argument("--model", help="the check's model for this run (default: the app's, agent.VERIFY_MODEL)")
    ap.add_argument("--note", default="", help="a few words for the log row (what this run tries)")
    args = ap.parse_args()
    cases = load(args.case)
    if not cases:
        print(f"no case {args.case!r}")
        return 2
    if args.model:
        os.environ["RUNSPECTOR_VERIFY_MODEL"] = args.model
    if args.no_rules:
        agent.VERIFY_PROMPT = BARE
    t0 = time.monotonic()
    work = [c for c in cases for _ in range(args.repeat)]
    with ThreadPoolExecutor(4) as ex:
        verdicts = list(ex.map(run_case, work))
    misses: dict[str, list[str]] = {}
    for case, got in zip(work, verdicts, strict=True):
        for n, g in zip(case["notes"], got, strict=True):
            if n["keep"] is not None and g != n["keep"]:
                want = "kept" if n["keep"] else "refused"
                misses.setdefault(case["id"], []).append(
                    f'{"kept" if g else "refused"} "{n["note"]}" (must be {want})')
    total = sum(1 for c in work for n in c["notes"] if n["keep"] is not None)
    wrong = sum(len(v) for v in misses.values())
    for cid, lines in misses.items():
        for line in sorted(set(lines)):
            print(f"MISS {cid}: {line}")
    print(f"{total - wrong}/{total} verdicts right, {len(cases)} cases x {args.repeat}, "
          f"{time.monotonic() - t0:.0f} s{' (NO RULES)' if args.no_rules else ''}")
    model = os.environ.get("RUNSPECTOR_VERIFY_MODEL") or agent.VERIFY_MODEL
    if args.case is None:
        prompt_id = hashlib.sha256(agent.VERIFY_PROMPT.encode("utf-8")).hexdigest()[:8]
        new = not LOG.exists()
        with LOG.open("a", encoding="utf-8") as log:
            if new:
                log.write("# The check before a fact is kept - every full run\n\n"
                          "| when | model | prompt | repeats | right | misses | note |\n"
                          "|---|---|---|---|---|---|---|\n")
            log.write(f"| {datetime.now().astimezone():%Y-%m-%d %H:%M} | {model} | "
                      f"{'NO RULES' if args.no_rules else prompt_id} | {args.repeat} | "
                      f"{total - wrong}/{total} | {'; '.join(sorted(misses)) or '-'} | {args.note} |\n")
    if args.no_rules:
        passing = [c["id"] for c in cases if c["id"] not in misses]
        print(f"still right without the rules ({len(passing)} of {len(cases)}): {', '.join(passing) or '-'}")
        return 0
    if args.case is None and not args.model:          # the app's own model only; others go to the log
        rows = "\n".join(f"| {cid} | {'<br>'.join(sorted(set(v)))} |" for cid, v in misses.items())
        RESULTS.write_text(
            f"# The check before a fact is kept - last run\n\n"
            f"{datetime.now().astimezone():%Y-%m-%d %H:%M %Z}, model `{model}`, {len(cases)} cases "
            f"x {args.repeat} repeats: **{total - wrong}/{total} verdicts right**.\n\n"
            + (f"| case | miss |\n|---|---|\n{rows}\n" if misses else "No miss.\n"),
            encoding="utf-8")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
