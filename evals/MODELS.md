# Which model writes the coach's cards

Measured 2026-11-02, before the coach cards moved from the CLI's default (Opus) to `sonnet`
(`web/agent.py` `COACH_MODEL`; `RUNSPECTOR_MODEL` overrides). The reason was the time a card
takes: a change asked in words waited for the answer, and the maintainer wanted "the latency of
these requests better". Every run below used the same judge (`sonnet`) and, per suite, the same
prompts and cases.

## The coach's judgement - `evals/cases.yaml`, 38 cases

| model | right | seconds per case (median / most) |
|---|---|---|
| Opus | 36/38 | 32 / 63 |
| Sonnet | 36/38 | 16 / 26 |
| Haiku | 26/38 | 17 / 33 |

Haiku failed, among others, the medical boundary and which hard session the history calls for -
the judgement the app exists for. It stays out.

## Conversations end to end - `evals/conversations.yaml`, 7 stories, 19 steps

| model | right | a card took (median / most) |
|---|---|---|
| Opus | 18/19 | 63 s / 128 s |
| Sonnet | 19/19, 19/19 | 14-16 s / 39 s |
| Haiku | 17/19 | 22 s / 85 s |

Before the prompts were made explicit, Sonnet scored 7-9/19 here while Opus scored 19/19. What
it did differently, and what changed (`web/server.py`, `web/agent.py`):

- It skipped the optional `"remember"` in the frame: nothing the athlete said about themselves
  was kept. A short line right after the athlete's words now says it (`REMEMBER_NUDGE`).
- In a question on a card it answered from the conversation alone, without a tool call, and the
  card was discarded (one turn = no data). The follow-up task now asks for
  `get_training_readiness` first.
- "Friday instead of Saturday" with a workout the app did not make: it gave up instead of
  replacing it. The change rule and the tool's refusal now say how.
- It called a filed change "done" or said a session "is now" moved, and once wrote that an
  athlete's blanket consent "is noted". The same line says a change waits for their tap, and
  that their yes is that tap.
- Once it answered in prose: an answer without a card is now run once more, told why.

After that: 16, 18, 19, 18, 17, 19, 19, 19 of 19 over eight runs while the last fixes went in, and
the two final runs above. The full history of every run is in `CONVERSATIONS_LOG.md`.
