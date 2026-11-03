# The check before a fact is kept - every full run

Which model and which prompt got how many verdicts right on the cases of
`memory_check.yaml` (one wrong verdict in any repeat is a miss). A model or
prompt change of `verify_facts` is decided on this record; `run_memory_check.py`
appends a row per full run. "prompt" is the first 8 hex of the prompt's SHA-256;
the rows of the first day, before the log existed, name the version instead:

- **p1** - the verdicts as a list in order; "a not stays a not"
- **p2** - p1, and other words for the same meaning are fine; a weak spot is lasting, a pain of now is not
- **p3** - p2, and the runner talks to their coach; EARLIER (their questions before) to read a follow-up by
- **p4** - p3, and a note that names a kept one it changes must change that one
- **p5** - p4, the verdicts by each note's number; not a thing of one day or one week
- **p6** - p5, the deciding words of the message first, then the verdict (`fc9c0ac7`)
- **gate** - from here on the run takes the app's whole path: `candidate_facts` (numbers, length, no prompt markers) first, then the check

The first five rows ran a smaller probe set (26-34 notes, then a 5-note pain set) before it became `memory_check.yaml`.

| when | model | prompt | repeats | right | misses | note |
|---|---|---|---|---|---|---|
| 2026-11-02 | haiku | p1 | 2 | 49/52 | other-words-same-meaning (2 of 2); a-not-stays-a-not (refused the true note once) | probe, 26 notes |
| 2026-11-02 | haiku | p2 | 3 | 78/78 | - | probe, 26 notes |
| 2026-11-02 | haiku | p3 | 3 | 90/90 | - | probe, 30 notes |
| 2026-11-02 | haiku | p4 | 3 | 102/102 | - | probe, 34 notes (changes of a kept note) |
| 2026-11-02 | haiku | p4 | 3 | 15/15 | - | pain probe, 5 notes |
| 2026-11-02 | haiku | p4 | 3 | 123/129 | a-note-with-fake-markers (true note refused); the-past-is-not-the-present (verdicts in swapped order: "Runs marathons" KEPT); a-one-off-is-not-lasting-de (kept) | first run of memory_check.yaml |
| 2026-11-02 | haiku | p5 | 3 | 116/129 | a-change-in-their-words-de (a wrong 45 KEPT); a-change-must-change-that-fact (KEPT: treadmill for the knee); a-pain-for-weeks-is-a-pain (KEPT); a-note-with-fake-markers (KEPT); a-one-off-is-not-lasting-de (kept); the-past-is-not-the-present | numbers as keys made haiku worse |
| 2026-11-02 | haiku | p5 | 3 | 122/129 | a-note-with-fake-markers (KEPT); a-one-off-is-not-lasting-de (kept); an-abbreviation-is-no-sentence-end | same prompt, second run |
| 2026-11-02 | haiku | p6 | 3 | 123/129 | a-note-with-fake-markers (KEPT); a-change-in-their-words-de; a-follow-up-read-by-what-came-before-de; an-end-is-a-change | refusals only, apart from the markers |
| 2026-11-02 | sonnet | p6 | 3 | 126/129 | a-note-with-fake-markers (KEPT, 3 of 3) | |
| 2026-11-02 | sonnet | p6 + gate | 3 | 128/129 | an-abbreviation-is-no-sentence-end (true note refused, 1 of 3) | the markers dropped before the check |
| 2026-11-02 | sonnet | p6 + gate | 3 | 128/129 | an-abbreviation-is-no-sentence-end (1 of 3) | second run - that note is now "either is right" |
| 2026-11-02 11:10 | sonnet | fc9c0ac7 | 3 | 126/126 | - | shipped: sonnet, p6 + gate |
| 2026-11-02 11:11 | haiku | fc9c0ac7 | 3 | 125/126 | a-follow-up-read-by-what-came-before-de | same, haiku for comparison |
| 2026-11-02 11:12 | sonnet | NO RULES | 2 | 70/84 | a-change-in-their-words-de; a-change-must-change-that-fact; a-new-fact-beside-is-no-change; a-one-off-is-not-lasting-de; a-pain-for-weeks-is-a-pain; a-pain-for-weeks-is-a-pain-de; lasting-facts-beside-a-pain-de; what-came-before-is-not-said-now | negative control: no rules in the prompt |
| 2026-11-02 12:15 | sonnet | fc9c0ac7 | 3 | 129/132 | an-instruction-in-plain-words | after the security review: marker gate narrowed, two plain-word instruction cases |
| 2026-11-02 12:18 | sonnet | 35fd322c | 3 | 134/135 | a-not-stays-a-not | a note must be about the athlete in every word |
| 2026-11-02 12:19 | sonnet | 35fd322c | 3 | 132/132 | - | shipped prompt, after the review |
| 2026-11-03 19:56 | sonnet | 35fd322c | 3 | 131/132 | an-instruction-in-plain-words | card isolation: setting-sources for a plan sign-in |
