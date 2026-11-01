# Roadmap

What the app would still need to be a coach rather than a very good daily adviser, in the order
of what each item does for a runner who uses it every day - not in the order of effort. Written
2026-10-28 from a review of v0.2.2 against the code; each gap names where it lives. Reordered
2026-10-30 after a second review against daily use, which added three gaps (3, 8, 10), widened
two (2, 14) and ordered the rest by what each does for the runner. Extended 2026-11-01 by a third
review, which checked every claim here against the code, corrected three that the data had
overtaken (6, 13, and how a run felt in 2), added seven gaps (15-21), moved one limit from
"Smaller" into a gap of its own (15) and split what is left from what has shipped.

The numbers are names, not ranks: an item keeps its number when the order changes, so a commit or
a release note that cites one still points at the right section. The order is the table's.

## What is left, by benefit

| # | Gap | For the runner | Effort |
|---|---|---|---|
| 15 | The light rests a day on one weak signal, and holds days easy for too little load | high (every morning) | small in code, medium in evals |
| 6 | The proposed session matched against the run that followed, and the same reps compared across weeks | high | medium |
| 16 | A week planned and put on the watch without Claude | high (anyone without Claude Code) | small to medium |
| 5 | A session with your own structure, in your own words | high | medium |
| 7 | Session sizes from the athlete's own time per weekday and usual run lengths - *short days shipped in v0.6* | medium to high (every planned week) | small |
| 2 | How a run felt - *the rest of the item is in v0.7* | medium | small |
| 17 | The planned week's load shown before the yes | medium (trust, and a check on the planner) | small |
| 18 | What kind of strength day it was: legs, upper body, whole body | medium (runners who lift) | small |
| 19 | A planned session moved to another day | medium | medium |
| 20 | A fitness trend beside VO2max: aerobic decoupling on steady runs | medium | medium |
| 13 | Deeper history on first install | medium (install day only) | medium |
| 21 | Installed from Claude Desktop in one file | medium (install day, Claude Desktop users) | medium |
| 14 | Race preparation towards a goal race: base, build and taper, with the race itself known | medium (those with a date) | high |

Why this order: the light (15) acts every morning for every runner, and its heaviest word, a rest
day, can hang on a single cut-off that the app itself grades as having no trial behind it - so it
comes first although the code is small. Matching the run to its session (6) comes next because it
is how the app learns whether its own prescriptions work, and the exact link turned out to be in
the data already. A week on the watch without Claude (16) is the difference between a plan and
none for a runner without Claude Code and changes nothing for one with it, so it follows the two
items every runner gets and comes before your own structure (5), which helps those who want what
the menu does not hold. Session sizes from the athlete's own time (7) shape every planned week. How
a run felt (2) is cheap now that the watch's own answer is in the data. The week's projected load
(17) is small and guards the yes. Strength days (18), moving a session (19) and a second fitness trend (20) help part of
the runners; deeper history (13) and the one-file install (21) help on install day only; race
preparation (14) stays last for the reasons in its section.

## Shipped

| # | Gap | In |
|---|---|---|
| 1 | Progression without a plan: a brake, a down week when due, a measurement when due, the next stimulus, a return week | v0.3 |
| 2 | A way back from the athlete: ill, pain, sore, stressed - with a state that lasts until it is over (how it felt is still open, above) | v0.7 |
| 3 | An easier option for every hard session in the plan, computed by the rules | v0.6 |
| 4 | Rep targets by pace, not heart rate, once the pace is real | v0.6 |
| 7 | Short weekdays kept short (the time per weekday is still open, above) | v0.6 |
| 8 | Monotony and weekly strain as a second load signal beside ACWR | v0.6 |
| 9 | `runspector today` for the morning; a sync when the page comes back into focus | v0.6 |
| 11 | Garmin's own readiness beside the app's | v0.6 |
| 12 | Body weight in the sync | v0.6 |
| 10 | Where each statement on a card comes from, and a record of what the AI read for it | v0.8 (on `main`, not yet released) |

Each from 2 on keeps its section below, with what it does and what it left open, because the reasoning behind
the order is the point of this file. The order they had, and why: progression without a plan (1)
came first, "very high" for the runner. The easier option (3) helps on every hard day and costs
almost nothing, because the rules already know the easy version of each session; it came before
pace targets (4) because it needs neither a chest strap nor a new measurement. Session sizes from
the athlete's own time (7) shape every planned week, so they came before monotony (8), which for
most hobby runners rarely fires. Of the trust items, where a statement comes from (10) touches every
card and so came before Garmin's own number (11).

## 2. A way back from the athlete

*Shipped in v0.7:* four buttons under today's call (ill, pain, sore legs, stressed) and the `check_in` tool, which a card run cannot call. A check-in holds until the athlete says it is over, or runs out after 3 days without a new word (sore legs: 2). Ill or pain rest the day whatever the light says, also before the morning's sync, and once over, as many easy days follow as it held (at most 7). Sore or stressed take the hard session but not the run. The week planner holds the week and puts no hard session before the earliest day the day rule could allow one; a single hard proposal on such a day is refused.

Still open: *how it felt*. A Garmin watch that asks after the run (Self Evaluation) stores the answer in the activity's detail - a feel and a perceived effort - so it belongs in the detail sync, which already fetches every run of the last 35 days; the app asks only after a run the watch did not ask about. Read beside the run's heart rate, a session that felt harder than its zones say is a signal the light does not have.

"Heavy legs", "a cold", "a rough week at work", "that was on a treadmill", a perceived effort:
none of it can be recorded. What is said in chat is gone with the chat; a card remembers only
the previous card of its kind and a thumb. Illness is the most common reason a plan has to
change, and the watch sees it only once HRV reacts. The coach's own instructions ask about
"falling weight" with nowhere for the athlete to say so. The You tab's note (v0.4.1) is a first,
free-text half of this: the coach reads it, no rule acts on it.

A check-in is not a single day. A cold has a start, days it is still there and an end; a sore
calf comes back two days later. So a check-in carries a state: since when, when it was last
said, and whether it is over - ended by the athlete ("feeling fine again") or by running out
after a set number of days without a new word. While `ill` or `pain` is open, the day is REST,
whatever HRV says; the morning after it ends is easy, not hard.

What it takes: a `check_ins` table (day, effort, flags, note, since, last said, over), two taps
on the Today tab (effort plus chips for ill / sore / stressed, and "over" on an open one), an MCP
tool that unattended card runs cannot call, and the open state as its own step in
`logic.decide_today` - in code, not in the prompt, and not in `readiness_verdict`, where a
check-in made before the morning sync would be lost.

## 3. An easier option for every hard session

*Shipped in v0.6:* `planning.easier_minutes` is the one rule (a hard session 10 minutes shorter and easy, at least 30; the long run one of the week's easy runs); the preview prints it, the watch gets it as the workout's description, the readiness tool names it for today's session and the red-day swap offers it.

The plan says "Thursday: 4×4 min VO2max". On Thursday the legs are heavy, the readiness is
yellow, or there are only 30 minutes - and the plan has nothing to say, so the session is either
forced or dropped. A coach would have said it in advance: "if you're tired, 30 minutes easy
instead", "if short on time, the long run at 60 % of its length".

What it takes: for every hard session and every long run, the alternative the rules would
choose on an EASY day (`logic` already knows it: the same day with the hard parts removed, or
the long run cut to the easy cap), shown as one line under the session in the week preview
(`planning.build_week`) and sent as the workout's description to the watch (`plan.py`; the
upload sets none today), so it is there at the start of the run. A scenario test: every hard
session has an alternative, and the alternative is never harder than the session.

## 4. Rep targets by pace

*Shipped in v0.6:* reps go by a pace band (± 5 s/km) once the pace has a source - a measured threshold or a usable race prediction; without one they keep the heart-rate zone.

VO2max and threshold reps go to the watch as heart-rate zones (`planning.py`, `hr_zone`). Heart
rate lags by 60–90 s, so on a 3–4 minute rep the watch spends a third of it saying "too low".
The usual practice is pace for reps and heart rate for easy running; the pace is already
computed. Since v0.3 the pace has a real source without a chest strap (Garmin's own 5 km and
10 km predictions), so this is now a method decision, and a small one.

## 5. A session with your own structure

"I have a 12 km loop, I want intervals on it" works today, but only as one of five kinds plus
a distance. The builder's rep menu is fixed (`planning.REP_MENU`), so "6×3", "10×400 m",
"8×1′/1′" cannot be asked for, a change request in a follow-up ("make it 6×3") runs into a tool
that cannot take it, there is no fartlek, no hills, no progression run, no tempo inside the long
run, distance and duration cannot both be given, and the app's form knows neither a day nor a
name although the tool does.

What it takes: a `structure` parameter the builder validates instead of chooses (rep length,
fits the route or the budget, spacing - the preview stays the gate), two or three more kinds,
and a free-text field beside the form whose words the agent turns into the structured call.

The model's part stays narrow: it turns the words into that structure and names zones, never
numbers. The numbers come from the athlete's own zones in code, and a zone the app cannot fill
becomes an open step, not a guess. A plain parse of the same words checks the model's: when the
total the athlete said and the steps disagree by more than a few minutes, the preview says so
instead of filing it.

## 6. The proposed session matched against the run, and the same reps across weeks

`runcoach_workouts` knows what was uploaded, `activities` knows what was run, and nothing links
them yet. "Analyze this run" does not know the run was Tuesday's 4×4, nor whether the reps reached
the zone-5 floor. Garmin's activity *list* carries no workout reference, but the activity *detail*
does: a run started from a scheduled workout names that workout's id (`associatedWorkoutId` in its
metadata). For the app's own sessions the link is therefore exact, not a guess. A run not started
from one - the athlete ran the session freehand - can be offered as a match by day and sport, which
the athlete confirms; a pair they reject stays rejected.

With the link, the week strip can say "as planned / deviated / not run", and the coach learns from
its own prescriptions. It also gives what no Garmin screen shows: the same session compared across
weeks - "4×4 min four weeks ago at 4:35/km and 172 bpm, today 4:28 at 170". Garmin's laps of a
workout run are the app's own steps, so rep 3 of one session is rep 3 of the other, no lap
guessing. Within a session, the rep where the pace falls off while the heart rate climbs names a
structure that started too fast. This is also how the app finds out whether its rules work for
more than the one athlete they have been run with.

What it takes: the detail sync reads the workout id (it already fetches every run of the last 35
days) into a column on `activities`; the reps from `activity_splits` keyed by the workout's steps;
a match table for the confirmed freehand runs; the comparison under "analyze this run" and on the
Trend tab, only between sessions of the same kind and structure.

## 7. Session sizes from the athlete's own time

*Short days shipped in v0.6:* a weekday whose runs are all clearly shorter than the others (≤ 80 % of their median, four runs or more) keeps every session there to its longest run and takes no quality session while another day can. Still open: the athlete setting the time per weekday on the You tab, and the proportions of the whole week from their own run lengths.

v0.4 reads the athlete's days from their history - how many, which weekdays,
the long-run day, a weekly other sport (`habits.py`). It does not yet read how
long they have on each of those days: a runner whose Tuesday runs never go past
40 minutes can still be planned 55 there, and the easy runs and the long run
keep the proportions of the built-in week, scaled to the budget. The data is
there (durations per weekday); the reason it waited is that it would be a
second sizing rule beside `_week_sizes` and `_session_cap`, and every rendered
eval fixture would move with it. Done right it replaces the built-in
proportions with the athlete's own, under the same budget and the same cap.

## 8. Monotony and weekly strain

*Shipped in v0.6:* above 2.0 with a week at least as heavy as the four before, the next week is held; the Trend tab shows the number, the coach hears it only when it is high.

ACWR compares this week's load with the last four; it does not see how the load is spread. Seven
similar medium days and a week of three hard days and four rest days can carry the same total
and the same ACWR, but the first leaves no day to recover. Foster's monotony (the week's mean
daily load divided by its standard deviation) and strain (weekly load times monotony) name that:
high monotony together with high strain is the pattern linked to illness and staleness.

What it takes: a pure function in `logic.py` over the daily loads the store already has, a brake
in `planning.py` with its own assumption line ("your last week was very even and heavy - Sunday
easy, Wednesday off"), one row on the Trend tab. The thresholds come from the literature and are
checked before they are used; a scenario test red against the old code first. For most hobby
runners it will rarely fire - which is why it is item 8, not item 2.

## 9. The morning

*Shipped in v0.6:* `runspector today [--sync]` (exit 3 without values for today), and a return to the page after more than `RUNSPECTOR_AUTOSYNC_MIN` minutes syncs first.

The product is needed at 07:00. There is no `runspector today` printing the readiness text (a
cron plus any notifier would do the rest), and the web app syncs only at start and on ↻, so a
page left open overnight shows yesterday until someone clicks.

## 10. Where each statement on a card comes from

*Done for v0.8 (on `main`, not yet released), wider than planned:* every point on a card carries
where it comes from - *your data* (with the tool it names), *you said*, *coach's reading* - and the
app keeps a record of everything the coach read for the card (`evidence.py`): the card run's whole
session (`--output-format stream-json`), each tool with what came back, and what the run was given.
*How this card was made* under the card shows it, and a check in code looks for every number on the
card in what the coach read; a point marked as your data that names a tool it never called, or holds
no number from the data, says so under it. The check reports and never rejects a card: it is
literal, so a number found was in front of the coach - not proof it was read rightly - and a
number not found may be the coach's own arithmetic. Chats in Claude Code or Claude Desktop are
recorded by the MCP server, one line per tool call, and the You tab lists them. Still open: chat
answers carry no marks (the record shows what they read, not which sentence came from where), and
the evals do not cover the card frame at all, so a change to it is checked by a real card run.

A card says "your HRV has been low for four days", "you said Tuesdays are short", "your long run
is too fast". The first is measured, the second is the athlete's own word, the third is the
coach's reading - and on the card they look the same. A reader cannot tell a number from an
opinion, and neither can the eval judge.

What it takes: the card frame asks for the source of every claim in three kinds - measured (the
watch, with its date), from you (the You tab, the note, a check-in), derived (a rule or the
coach's reading, with what it was derived from) - shown as a small mark beside the bullet
(`skills/coach.md`, the card frame in `web/agent.py`, `cards.js`). An eval case checks it: a
claim marked measured that no tool returned fails the card.

## 11. Garmin's own readiness

*Shipped in v0.6:* Garmin's morning Training Readiness beside the light on the Today tab, in the readiness tool and in `runspector today` - on the watches that compute it (a fenix 6S does not). No rule reads it.

Every Garmin user will ask "Garmin says 85, you say EASY - why?", and the app cannot answer
because it never fetches the number. Showing both would cost two columns and buy trust.

## 12. Body weight

*Shipped in v0.6:* one weigh-in call per sync (56 days, kg, nothing else of the body composition); under the VO2max chart and in the VO2max tool with how much the weight alone moves a per-kilogram value.

VO2max is ml/kg/min: two kilograms move it by about a point with no change in fitness. Garmin's
weigh-ins are not fetched, so the one confounder every athlete can name is not in the picture.

## 13. Deeper history on first install

The activity list already goes back 26 weeks (since v0.4.1) and run details 35 days, but the
daily metrics - where the VO2max history lives - start with 30 days on the first sync and cap at
90, and the Trend tab draws 56. On install day "why is my VO2max not moving?" cannot be answered -
the card says so itself. A one-off, throttled deep backfill of the daily metrics would give the
founding question its data.

## 14. Race preparation

A base → build → taper block towards a date, with the long run growing and the last two weeks
cutting volume while keeping intensity. It needs a race date and block state, and it is for the
runner with a race; for everyone else v0.3's rolling rules do what a block would have done
without the calendar - which is why this sits last, not first.

The race itself belongs on the You tab: a name, a date (also a rough one - "end of September"
is enough to plan the base), a priority (A: the race the block is built for; B and C: raced on
the way, with a short taper or none) and a target time, which sets the paces once it is closer
than the predictions. Where the race and the athlete's week disagree - the race is on a Sunday
and Sunday is a day off, or the target time needs more weekly running than the runs per week
allow - the app asks one question before it plans, and holds the plan until it is answered. The
block stays code; the question is the only place the conversation shapes it.

## 15. The light and one weak signal

The light has one rule for its strongest word: one REST signal makes the day REST
(`logic.readiness_verdict`, the table in `skills/zones.md`). A sleep score of 49 after one bad
night, or a Body Battery peak of 39, rests the day on its own - and for those cut-offs, and the
resting-HR ones, `zones.md` itself says no trial stands behind them; sleep score and Body Battery
are Garmin's own composites. The step between 49 and 50 is not the fault: any rule has one, and a
smooth score would cost the light the one thing it has over a vendor's number, that the athlete can
see why. The fault is how much a single soft signal decides. For a runner whose risk is running too
little rather than too much, a rest day for a weak reason costs more than it protects.

The other half sits on the load axis. An ACWR under 0.8 with more than two days since a hard
session dampens the day to EASY ("stimulus missing"), which answers too little load with less of
it. After a few days off - a cold, a sore calf - it keeps every day easy until the ratio recovers:
two to three weeks of easy running for the simulator's runner with a sore calf, on top of the easy
days the check-in already held.

What it takes: REST from the strong signals - HRV status LOW or POOR (Garmin's own, against the
athlete's baseline), resting HR +7 or more, an open ill or pain check-in - or from the two weak
rest signals together; a sleep score under 50 or a Body Battery peak under 40 alone makes the day
EASY. ACWR above 1.5 drops to EASY whatever its source, since the spike claim is the disputed part.
ACWR under 0.8 stays on the Trend tab and in the readiness text as context and dampens no day; what
to do about a missing stimulus is the planner's question (`next_stimulus`), not the light's. Before
the change the literature is read again - how HRV-guided training trials answer a drop, and the
evidence on undertraining - and every cut-off gets its grade in `zones.md`. A scenario test red
against the old rule first, the sweep, the golden week, and a full eval run, since the readiness
text in the fixtures changes.

## 16. A week on the watch without Claude

Without Claude the app reads, decides and shows: the light, the runs, the trends, the sync. What it
cannot do is the part a runner feels most - a week of sessions on the watch - although nothing in
that part needs a model. `planning.build_week` builds the week in code, `propose_week` files it,
the preview is the app's, and `plan.apply` puts it on the watch with read-back and undo. Today the
only way to `propose_week` is the coach, a card or a chat, so a runner without Claude Code gets the
rules' verdict every morning and never the week the same rules would write.

What it takes: a "Plan my week" button that calls the same `propose_week` the MCP tool calls and
shows the proposal the coach would have filed - the sessions, the easier option under each hard
one, the assumptions - with the same "Put on watch" and the same undo. The check-in gate, the 48
hours and the progression live in code, so they hold on this path unchanged, and the write path
itself does not change. A test: the button's proposal equals the tool's for the same day. The
README's "Without Claude" then names the coach's words as the one thing that needs it.

## 17. The planned week's load before the yes

The planner caps each week (+10 %, at most 1.1 × the larger of the last two) and holds it after a
monotonous heavy one, but the preview shows minutes, not what those minutes would do to the app's
own load signals. A runner about to say yes cannot see "after this week your load ratio would sit
near 1.4" - and neither can a test.

What it takes: each planned session's load estimated from the athlete's own load per minute for
that kind of session over the last weeks, laid over the real days, so the preview can say where the
computed ACWR and the monotony would land at the end of the week if it is run as planned (Garmin's
own ratio cannot be projected); a line only when one would cross its band (1.3, 2.0). A property
test over the simulator's runners: no week planned from a steady history projects above 1.5 - if
one does, the planner's cap is wrong, not the line.

## 18. What kind of strength day it was

A day with another sport keeps the quality run off that day and the next (`habits.py`, the You
tab's sport days), whatever the session was. A heavy leg session the day before threshold reps is
what costs the reps; an upper-body session costs them little, yet it blocks the same two days.
Garmin cannot tell the two apart for the app: its heart-rate load scores a strength session far
below a run, so ACWR barely sees one, and the watch's exercise recognition leaves many sets unnamed.

What it takes: the athlete names it. On the You tab each sport day gets "legs", "upper body" or
"whole body"; whole body is the default and today's rule. Legs and whole body keep today's rule;
upper body keeps the quality run off that day, not the next. A profile key with its `validate`,
the row in `you.js` (`WEEK_ROWS`) and the planner's reading in `habits.week`; a scenario test red
first.

## 19. A planned session moved to another day

"Thursday's intervals on Friday" is the most common change to a week, and the app cannot take it.
A red day can swap the hard session for its easier version and a week can be planned again, but a
single session cannot be moved.

What it takes: a move that checks the new day against the same rules as a planned one - 48 hours
between hard stimuli, the check-in gate, sport days and days off - and refuses with the reason when
it fails. On Garmin it is the app's own unschedule and schedule, never a delete (`plan.py`); a run
already done is not moved; a day that already holds a session asks first. Undo as for every apply.
It is on the write path, so it gets the deep review.

## 20. A fitness trend beside VO2max

Garmin's VO2max moves only when a run re-estimates it; between such runs the carried-forward value
says nothing (`get_vo2max_history` shows only the days it changed). A runner asking "am I getting
fitter?" in a month of easy running has no second number. Aerobic decoupling is one: on a steady
run, the speed per heartbeat in the second half against the first. The smaller the drop, the better
the aerobic base holds - and it needs no new sensor, only the splits the app already stores
(`activity_splits`).

What it takes: runs of 45 minutes or more without reps, weighted by time, not by lap count; runs in
heat or with much climbing left out (the temperature and each split's elevation gain are stored);
the trend on the Trend tab beside VO2max. The usual 5 % line is a convention and is graded so in
`zones.md`; a single run is noise, so only the trend is shown.

## 21. Installed from Claude Desktop in one file

Today the MCP part takes a terminal: an install with `uv` or `pipx`, `runspector login`, then an
entry in Claude Desktop's JSON configuration. Claude Desktop can also install a local MCP server
from one bundle file (an MCP Bundle, `.mcpb`), and the bundle's `uv` server type lets the app ship
its `pyproject.toml` instead of a frozen environment. For a Desktop user the MCP part becomes one
file.

The hard part is the Garmin sign-in. The password and the MFA code must never pass through the
chat, and a bundle's settings are no place for a one-time code, so a server without a Garmin
session would start the web app's sign-in dialog (`auth.WebLogin`) on 127.0.0.1 and its first
answer would point there. The `uv` server type is still marked experimental; its state is checked
before anything is built. An installer of its own (Electron, Tauri) is not on this list: signing
and notarising for two systems would be a second product to keep alive.

## Smaller

- Cards are written in English whatever the athlete's language; a `RUNSPECTOR_LANG` would be cheap.
- Strength and cycling count as hard days and keep the quality run off their day and the next
  (good), but get no slot of their own in a planned week; what kind of strength day it was is 18.
- From four running days a week the planner sets two quality sessions. For a runner with a short
  or patchy history that is a lot; whether the second one should wait for a few consistent weeks
  is open.
- Menstrual-cycle data, which Garmin has, appears nowhere.
- Every proposal creates a new Garmin workout and nothing deletes one; after months the library
  is full of "VO2max 4x4 min".
- After ill or pain the app keeps as many days easy as it held, and that is all: no return schedule beyond it, deliberately.
- The weeks after an illness or a pain are sized from a median that holds the weeks it took out, so they
  climb back slowly (the synthetic runner: 265 min before, ~150 seven weeks later). A pause of ten days
  or more already rebuilds towards the level before it; a check-in of a few days could do the same.
- Near the shortest useful sessions a week planned as a step can come out a few minutes smaller than
  the one before it - a measurement week's steady block is shorter than the threshold session it
  replaces, or the same budget fills one day fewer - because the step floor compares budgets, not the
  sessions built from them, and a small week may sit up to 15 % above its budget.
- A pain check-in is one flag: no body part, no course. "Left calf, better for three days" would let
  the coach say more - picked by the athlete, never pulled from a note by the model.
- Reps by pace ignore the heat: on a hot day a pace band set from cooler runs is harder than the
  zone it stands for. The run's temperature is stored; a forecast would be the app's first call to
  anything but Garmin.
- The data directory is made 0700 only when the app creates it, and `runspector doctor` warns
  about a wider one; the database file itself takes the umask. 0600 on creation would hold where
  someone widened the directory.
- The evals run on a different model and in `--safe-mode`; a green suite is not the shipped coach.
- The simulator's runners since v0.6: one whose settings contradict themselves, one who says yes to
  the week three days late; since v0.7 one whose calf hurts after one quality session in seven.
