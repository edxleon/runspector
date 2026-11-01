// Pure logic of the coach cards (static/cards.js). cards.js imports ui.js; its
// token bootstrap catches the missing `location` itself. `renderCard` needs NO
// DOM (a pure string function) and is therefore tested too — the escaping
// invariant from the module header would otherwise rest on reading alone.
import test from "node:test";
import assert from "node:assert/strict";
import {
  truncate, modelShort, metaParts, avgDurationSec, fmtExpectation, groupCards,
  classify409, todayCard, splitBullets, textPlain, jobsForStrip, isActive, renderCard,
} from "../src/runspector/web/static/cards.js";

test("truncate", () => {
  assert.equal(truncate("short", 10), "short");
  const k = truncate("This is a long sentence with many words in it", 20);
  assert.ok(k.endsWith("…") && k.length <= 20, k);
  assert.ok(!/\s…$/.test(k), "no space before the ellipsis");
  assert.equal(truncate(null, 5), "", "null becomes empty, not 'null'");
});

test("modelShort", () => {
  assert.equal(modelShort("claude-opus-5"), "opus-5");
  assert.equal(modelShort("claude-sonnet-5-20260901"), "sonnet-5", "date suffix is dropped");
  assert.equal(modelShort(""), "");
  assert.equal(modelShort(undefined), "");
});

test("metaParts", () => {
  const fmt = { day: d => "D:" + d, ago: a => "A:" + a, num: (n, dig) => n.toFixed(dig) };
  let m = metaParts({ data_through: "2026-09-06", generated_at: "2026-09-06T07:45:01+02:00",
                      model: "claude-opus-5", cost_usd: 1.7 }, fmt);
  assert.equal(m[0], "data up to D:2026-09-06", "meta starts with the data status, not with '3 h ago'");
  assert.equal(m[1], "generated A:2026-09-06T07:45:01+02:00");
  // No price on the card: `cost_usd` is in the input above and must NOT appear.
  assert.deepEqual(m.slice(2), ["opus-5"]);
  m = metaParts({ day: "2026-08-13", written: "2026-08-13T08:16:23" }, fmt);
  assert.equal(m.join("|"), "D:2026-08-13|generated A:2026-08-13T08:16:23",
               "card without stamps: day/written as fallback, no model");
  assert.equal(metaParts({ cost_usd: null, model: "" }, fmt).length, 0, "nothing invented for an empty card");
});

test("avgDurationSec / fmtExpectation", () => {
  const J = (status, started, finished) => ({ id: "j", status, started, finished });
  const jobs = [
    J("done", "2026-09-06T07:45:00+02:00", "2026-09-06T07:47:00+02:00"),   // 120 s
    J("done", "2026-09-06T08:00:00+02:00", "2026-09-06T08:04:00+02:00"),   // 240 s
    J("failed", "2026-09-06T09:00:00+02:00", "2026-09-06T09:30:00+02:00"), // does not count
    J("running", "2026-09-06T10:00:00+02:00", null),
    J("done", null, "2026-09-06T11:00:00+02:00"),                          // no `started`
  ];
  assert.equal(avgDurationSec(jobs), 180, "average only over finished runs with both stamps");
  assert.equal(avgDurationSec([]), null);
  assert.equal(avgDurationSec(null), null);
  assert.equal(fmtExpectation(180), "~3 min");
  assert.equal(fmtExpectation(44), "~40 s", "seconds rounded to tens");
  assert.equal(fmtExpectation(3), "~10 s", "never below 10 s");
  assert.equal(fmtExpectation(null), "");
});

test("groupCards", () => {
  const C = (id, extra = {}) => ({ id, job_id: id, kind: "train-today", ...extra });
  const cards = [C("j-3", { parent: "j-1" }), C("j-2", { parent: "j-1" }), C("j-1"),
                 C("j-0", { parent: "j-gone" })];          // orphan: parent not in the list
  let g = groupCards(cards);
  assert.equal(g.main.map(c => c.id).join(), "j-1,j-0", "main list: parent + orphan, source order");
  assert.equal(g.children.get("j-1").map(c => c.id).join(), "j-2,j-3", "children oldest first");
  assert.ok(!g.children.has("j-gone"), "unknown parents create no entry");
  g = groupCards([C("j-5", { parent: "j-5" })]);
  assert.equal(g.main.length, 1, "a self reference lands in the main list");
  assert.equal(groupCards([]).main.length, 0);
  assert.equal(groupCards(null).main.length, 0);
});

test("classify409 switches on the code, never on the message", () => {
  let k = classify409({ code: "already_running", ref: "j-20260906-074501-a44a", message: "whatever" });
  assert.deepEqual(k, { type: "running", ref: "j-20260906-074501-a44a" });
  k = classify409({ code: "card_current", ref: "c-1" });
  assert.deepEqual(k, { type: "current", ref: "c-1" });
  assert.equal(classify409({ code: "queue_full" }).type, "queue_full");
  // The thrown error carries the raw body in `data` as well.
  assert.equal(classify409({ data: { code: "card_current", ref: "c-2" } }).ref, "c-2");
  assert.equal(classify409({ message: "already running (j-1)" }).type, "other",
               "message text alone decides nothing");
  assert.deepEqual(classify409(undefined), { type: "other", ref: null });
});

test("todayCard", () => {
  const H = (id, day, gen, extra = {}) => ({ id, job_id: id, kind: "train-today", day,
                                             generated_at: gen, ...extra });
  const cards = [
    H("j-b", "2026-09-06", "2026-09-06T07:45:01+02:00"),
    H("j-c", "2026-09-06", "2026-09-06T09:00:00+02:00", { parent: "j-b" }),   // an answer does not count
    H("j-a", "2026-09-05", "2026-09-05T07:45:01+02:00"),
    { id: "j-x", job_id: "j-x", kind: "week-review", day: "2026-09-06" },
  ];
  let h = todayCard(cards, "2026-09-06");
  assert.ok(h && h.card.id === "j-b" && h.current === true);
  h = todayCard(cards, "2026-09-07");
  assert.ok(h && h.card.id === "j-b" && h.current === false, "next day: same card, but not current");
  assert.equal(todayCard([cards[3]], "2026-09-06"), null, "other kind → null");
  assert.equal(todayCard([], "2026-09-06"), null);
  h = todayCard([H("j-old", "2026-09-06", "2026-09-06T06:00:00+02:00"),
                 H("j-new", "2026-09-06", "2026-09-06T12:00:00+02:00")], "2026-09-06");
  assert.equal(h.card.id, "j-new", "same day: the later generated_at wins");
});

test("splitBullets", () => {
  const bl = ["a", "b", "c", "d", "e", "f", "g", 7, ""];
  let t = splitBullets(bl);
  assert.equal(t.visible.length, 2);
  assert.equal(t.rest.join(""), "cdefg", "non-strings and empties are dropped");
  t = splitBullets(bl, 5);
  assert.equal(t.rest.join(""), "fg");
  assert.equal(splitBullets(["a", "b"]).rest.length, 0);
  assert.equal(splitBullets(null).visible.length, 0);
});

test("textPlain", () => {
  assert.equal(textPlain("# Briefing\r\n\r\n**Sessions:** 3 today\n\n\n\n- point"),
               "Briefing\n\nSessions: 3 today\n\n- point");
  assert.equal(textPlain("<b>x</b>"), "<b>x</b>", "textPlain does NOT escape — esc() does that on render");
  assert.equal(textPlain(null), "");
  assert.equal(textPlain("*Briefing* · Wednesday"), "Briefing · Wednesday", "single-asterisk bold goes");
  assert.equal(textPlain("_34 items in total_"), "34 items in total", "underscore italics go");
  assert.equal(textPlain(["```", "Unclear: X  -182.75", "```"].join("\n")), "Unclear: X  -182.75");
  // The more important group: what must NOT be touched.
  assert.equal(textPlain("3 * 4 = 12"), "3 * 4 = 12", "a single asterisk stays (multiplication)");
  assert.equal(textPlain("snake_case_name stays"), "snake_case_name stays");
});

test("jobsForStrip / isActive", () => {
  const now = new Date("2026-09-06T12:00:00+02:00").getTime();
  const jl = [
    { id: "r", status: "running", started: "2026-09-06T11:58:00+02:00" },
    { id: "q", status: "queued", created: "2026-09-06T11:59:00+02:00" },
    { id: "f-new", status: "failed", finished: "2026-09-06T03:10:00+02:00" },   // 9 h old
    { id: "f-old", status: "failed", finished: "2026-09-04T03:10:00+02:00" },   // >24 h
    { id: "c", status: "cancelled", finished: "2026-09-05T13:00:00+02:00" },    // 23 h old
    { id: "d-new", status: "done", finished: "2026-09-06T08:00:00+02:00" },
    { id: "d-old", status: "done", finished: "2026-09-01T08:00:00+02:00" },
    { status: "running" },                                                       // no id
  ];
  assert.equal(jobsForStrip(jl, { now }).map(j => j.id).join(), "r,q,f-new,c");
  assert.equal(jobsForStrip(jl, { now, finished: true }).map(j => j.id).join(), "r,q,f-new,c,d-new");
  assert.equal(jobsForStrip(jl, { now, max: 2 }).length, 2);
  assert.ok(isActive({ status: "queued" }) && !isActive({ status: "done" }) && !isActive(null));
});

test("renderCard escapes everything that comes from a model, a user or Garmin", () => {
  const EVIL = "<img src=x onerror=alert(1)>";
  const card = {
    id: "c-1", job_id: "j-1", kind: "train-today", day: "2026-09-07",
    generated_at: "2026-09-07T07:46:00+02:00", model: "claude-opus-5",
    headline: EVIL, verdict: EVIL, bullets: [EVIL, "second " + EVIL],
    delta: EVIL, ref: EVIL,
    feedback: { value: "good", text: EVIL, ts: "2026-09-07T08:00:00+02:00" },
  };
  const openHtml = renderCard(card, { open: true });
  assert.ok(typeof openHtml === "string" && openHtml.length > 0, "renderCard works without a DOM");
  assert.ok(!openHtml.includes("<img src=x"));
  // NOT a check for "onerror=alert": after escaping that legitimately stands in
  // the card as PLAIN TEXT. The invariant is that no `<` of the payload
  // arrives as markup.
  const known = /<(?:\/?)(?:article|button|span|h3|div|p|details|summary|input)[^>]*>/gi;
  assert.ok(!/<img|<script|<\/?[a-z]+ [a-z-]*=/i.test(openHtml.replace(known, "")), "no foreign markup");
  assert.ok(openHtml.includes("&lt;img"), "the text is there, only escaped");
  assert.ok(openHtml.includes('aria-pressed="true"'), "feedback value 'good' marks its button");

  const closedHtml = renderCard(card, { open: false });
  assert.ok(!closedHtml.includes("<img src=x") && closedHtml.includes("&lt;img"));
  const withChild = renderCard({ ...card, feedback: null },
                               { open: true, children: [{ ...card, id: "c-2", parent: "c-1" }] });
  assert.ok(!withChild.includes("<img src=x"), "child cards escape as well");
  const withRef = renderCard(card, { open: false, refText: () => EVIL });
  assert.ok(!withRef.includes("<img src=x"), "a refText result is escaped");
});

test("renderProposal: the preview is escaped, the button is the only write, and it says why it is off", async () => {
  const { renderProposal } = await import("../src/runspector/web/static/cards.js");
  const p = { id: "p-20260922-101010-abcd", day: "2026-09-24", status: "open",
              preview: "VO2max 4x4 min <img src=x onerror=alert(1)>\n  warmup  2.6 km  no target",
              warnings: [] };
  const html = renderProposal(p, {});
  assert.ok(html.includes("&lt;img"), "the preview is escaped, not rendered");
  assert.ok(html.includes('data-apply="p-20260922-101010-abcd"'), "the apply button carries the id");
  assert.ok(!html.includes("disabled"), "with a session the button is live");

  const off = renderProposal(p, { canApply: false, applyHint: "demo data - no Garmin account" });
  assert.ok(off.includes("disabled") && off.includes("demo data"), "off, and it says why");

  const done = renderProposal({ ...p, status: "applied", workouts: [900001] }, {});
  assert.ok(!done.includes("data-apply") && done.includes("On Garmin"), "applied: no button, a pill");
  assert.ok(done.includes("workout 900001"));
  assert.equal(renderProposal(null, {}), "");

  const week = renderProposal({ ...p, days: 4 }, {});
  assert.ok(week.includes("Proposed week") && week.includes("4 sessions"), "a package says so");
  assert.ok(week.includes("The whole week goes"), "the arm label names the scale of the write");
  const weekDone = renderProposal({ ...p, days: 4, status: "applied", workouts: [1, 2, 3, 4] }, {});
  assert.ok(weekDone.includes("4 workouts"));
  // Applied is not the end of the story: taking it back off is the other half,
  // and a write that cannot be undone is a write nobody makes the first time.
  assert.ok(done.includes('data-undo="p-20260922-101010-abcd"'), "applied offers the way back");
  assert.ok(done.includes("Take off watch"));
  const offNoSession = renderProposal({ ...p, status: "applied", workouts: [900001] },
                                      { canApply: false });
  assert.ok(/data-undo[^>]*disabled/s.test(offNoSession), "and it is off without a login too");

  // "applied" is not "finished": three of four uploaded means one is still
  // waiting, and the pill used to take away the only button that could add it.
  const half = renderProposal(
    { ...p, days: 4, status: "applied", workouts: [1, 2, 3], pending: 1 }, {});
  assert.ok(half.includes("data-apply"), "a half-applied package keeps its button");
  assert.ok(half.includes("Put the remaining 1 on watch"));
  assert.ok(half.includes("3 of 4 on Garmin"), "and says how far it got");
});

test("renderProposal: a half-applied week can still come back off", async () => {
  // A package with two of four sessions on Garmin showed only "put the
  // remaining 2 on watch": the two that were already there had no way back
  // from the app, only from Claude Code.
  const { renderProposal } = await import("../src/runspector/web/static/cards.js");
  const p = { id: "p-20260921-090000-ab12", status: "applied", days: 4, pending: 2,
              day: "2026-09-21", preview: "…", workouts: [900001, 900002] };
  const html = renderProposal(p, {});
  assert.match(html, /data-apply="p-20260921-090000-ab12"/, "the rest can still be added");
  assert.match(html, /data-undo="p-20260921-090000-ab12"/, "and what is on can come off");
});

test("renderProposal: the button that writes says what changes and that it can be undone", async () => {
  const { renderProposal } = await import("../src/runspector/web/static/cards.js");
  const open = renderProposal({ id: "p-20260921-090000-ab12", status: "open", days: 1,
                                day: "2026-09-21", preview: "…" }, {});
  assert.match(open, /nothing else on your calendar changes/);
  assert.match(open, /Take off watch/);
  const done = renderProposal({ id: "p-20260921-090000-ab12", status: "applied", days: 1,
                                pending: 0, workouts: [900001], preview: "…" }, {});
  assert.doesNotMatch(done, /proposal-net/, "nothing left to write, nothing to reassure about");
});

test("todayCard: a card made before the athlete checked in is out of date", () => {
  const card = { kind: "train-today", day: "2026-06-10", snapshot_generated_at: "2026-06-10T05:00:00+00:00" };
  assert.equal(todayCard([card], "2026-06-10").outdated, false);
  assert.equal(todayCard([card], "2026-06-10", "train-today", "2026-06-10T06:00:00+00:00").outdated, true);
  assert.equal(todayCard([card], "2026-06-10", "train-today", "2026-06-10T04:00:00+00:00").outdated, false);
  assert.equal(todayCard([card], "2026-06-11", "train-today", "2026-06-10T06:00:00+00:00").outdated, false);
});

/* ── where a point comes from, and how a card was made ───────────────────── */

test("bulletItems: each point keeps its own source, also past an empty one", async () => {
  const { bulletItems } = await import("../src/runspector/web/static/cards.js");
  const items = bulletItems({ bullets: ["a", "", "c"],
                              sources: [{ kind: "measured" }, { kind: "you" }, { kind: "derived" }] });
  assert.deepEqual(items.map(x => [x.text, x.src?.kind]), [["a", "measured"], ["c", "derived"]]);
  assert.deepEqual(bulletItems({ bullets: ["old card"] }), [{ text: "old card", src: null }]);
});

test("sourceBadge: plain words, and the reason when 'your data' is not backed", async () => {
  const { sourceBadge } = await import("../src/runspector/web/static/cards.js");
  assert.equal(sourceBadge(null), null);
  assert.equal(sourceBadge({ kind: "opinion" }), null);
  const ok = sourceBadge({ kind: "measured", from: "today's readiness", called: true, unmatched: [] });
  assert.equal(ok.label, "your data");
  assert.equal(ok.warn, false);
  assert.match(ok.tip, /today's readiness/);
  assert.equal(sourceBadge({ kind: "you" }).label, "you said");
  assert.equal(sourceBadge({ kind: "derived" }).label, "coach's reading");
  const bad = sourceBadge({ kind: "measured", ref: "get_trend", called: false, unmatched: ["93.4"] });
  assert.equal(bad.warn, true);
  assert.match(bad.why, /get_trend, which the coach did not look at/);
  assert.match(bad.why, /93\.4 is not in what the coach looked at - it may be the coach's own arithmetic/);
  // a derived point may hold a number of its own making - no warning
  assert.equal(sourceBadge({ kind: "derived", unmatched: ["13.7"] }).warn, false);
});

test("evidenceSummary: what the coach looked at, and how many numbers it backs", async () => {
  const { evidenceSummary } = await import("../src/runspector/web/static/cards.js");
  assert.equal(evidenceSummary(null), "");
  assert.equal(evidenceSummary({ recorded: false }), "not recorded for this card");
  assert.equal(evidenceSummary({ recorded: true, reads: 1, numbers: 0, unmatched: [] }), "looked at 1 thing");
  assert.equal(evidenceSummary({ recorded: true, reads: 6, numbers: 16, unmatched: [{}, {}] }),
               "looked at 6 things · 14 of 16 numbers found there");
  assert.equal(evidenceSummary({ recorded: true, reads: 6, numbers: 9, unmatched: [] }),
               "looked at 6 things · all 9 numbers found there");
});

test("renderEvidence: every value from the record is escaped, and it says what it is", async () => {
  const { renderEvidence } = await import("../src/runspector/web/static/cards.js");
  const evil = "<img src=x onerror=alert(1)>";
  const html = renderEvidence({
    recorded: true, complete: true, model: "claude-opus-5",
    calls: [{ tool: evil, label: evil, args: evil, result: evil, changed: false, error: false },
            { tool: "apply_workout", label: "a session put on your watch", changed: true, result: "" }],
    inputs: { about_you: evil, task: evil, rules_sha256: "abcdef01feedbeef", app_version: "0.8.0" },
    check: { numbers: 3, in_data: 2, unmatched: [{ where: "headline", value: evil }] },
  });
  assert.doesNotMatch(html, /<img/, "no tag from the record reaches the page");
  assert.match(html, /&lt;img/);
  assert.match(html, /only through this app's own tools/);
  assert.match(html, /2 of 3 numbers on this card/);
  assert.match(html, /\(the headline\)/);
  assert.match(html, /A session put on your watch|a session put on your watch/);
  assert.match(html, /opus-5 · rules abcdef01 · app 0\.8\.0/);
  assert.match(renderEvidence({ recorded: false, old: true }), /before the app kept a record/);
  assert.match(renderEvidence("loading"), /Loading/);
  assert.match(renderEvidence({ error: "<b>x</b>" }), /&lt;b&gt;/);
  assert.match(renderEvidence({ recorded: true, calls: [], check: { numbers: 2, unmatched: [] } }),
               /Every number on this card \(2\)/);
});

test("renderCard: badges in front of the points, the reason under a doubtful one", () => {
  const html = renderCard({
    id: "j-1", kind: "train-today", headline: "h", verdict: "v",
    bullets: ["HRV <b>38</b>", "sleep 93.4"],
    sources: [{ kind: "measured", from: "today's readiness", called: true, unmatched: [] },
              { kind: "measured", ref: "get_trend", called: false, unmatched: ["93.4"] }],
    evidence: { recorded: true, reads: 1, numbers: 2, in_data: 1, unmatched: [{ where: "point 2", value: "93.4" }] },
  }, { open: true });
  assert.match(html, /class="src src-measured"[^>]*>your data<\/span> HRV &lt;b&gt;38/);
  assert.match(html, /src-warn[^>]*>! your data/);
  assert.match(html, /class="src-why">Marked as your data, but/);
  assert.match(html, /How this card was made · looked at 1 thing · 1 of 2 numbers found there/);
  assert.match(html, /data-evidence="j-1"/);
  const old = renderCard({ id: "j-2", kind: "train-today", headline: "h", bullets: ["b"] }, { open: true });
  assert.doesNotMatch(old, /class="src |How this card was made/, "an old card looks as it did");
});

test("evidenceSummary: counts every number not found, not only the listed ones", async () => {
  const { evidenceSummary } = await import("../src/runspector/web/static/cards.js");
  const listed = Array.from({ length: 12 }, (_, i) => ({ where: "point 1", value: String(i) }));
  assert.equal(evidenceSummary({ recorded: true, reads: 2, numbers: 30, missing: 18, unmatched: listed }),
               "looked at 2 things · 12 of 30 numbers found there");
});

test("renderEvidence: a record that was deleted is not called older than the record", async () => {
  const { renderEvidence } = await import("../src/runspector/web/static/cards.js");
  assert.match(renderEvidence({ recorded: false, gone: true }), /no longer there/);
  assert.doesNotMatch(renderEvidence({ recorded: false, gone: true }), /before the app kept/);
});

test("evidence: an incomplete record checks nothing, and says why a card has none", async () => {
  const { evidenceSummary, renderEvidence } = await import("../src/runspector/web/static/cards.js");
  assert.equal(evidenceSummary({ recorded: true, checked: false, reads: 3, numbers: 9, missing: 0 }),
               "looked at 3 things · record incomplete, numbers not checked");
  const cut = renderEvidence({ recorded: true, calls: [], check: { checked: false, numbers: 9 } });
  assert.match(cut, /too long to\s+keep whole/);
  assert.doesNotMatch(cut, /Every number|Not found there/);
  assert.match(renderEvidence({ recorded: false, old: true }), /before the app kept a record/);
  assert.match(renderEvidence({ recorded: false }), /could not record/);
});

test("evidenceToggle: a failed load is tried again only when the section is opened again", async () => {
  // A re-render inserts the section already open, and the browser fires
  // `toggle` for it: loading on an open toggle with a failed record in the
  // cache looped - fetch, fail, redraw, toggle, fetch - without end.
  const { evidenceToggle } = await import("../src/runspector/web/static/cards.js");
  assert.equal(evidenceToggle(true, undefined), "load", "opened, never tried");
  assert.equal(evidenceToggle(true, "loading"), null);
  assert.equal(evidenceToggle(true, { recorded: true }), null);
  assert.equal(evidenceToggle(true, { error: "server gone" }), null, "the redraw after a failure");
  assert.equal(evidenceToggle(false, { error: "server gone" }), "forget", "closed: the next opening tries again");
  assert.equal(evidenceToggle(false, { recorded: true }), null, "a loaded record is kept");
});

test("runningFor: a running job's clock, by the second", async () => {
  const { runningFor } = await import("../src/runspector/web/static/cards.js");
  const t0 = Date.parse("2026-11-01T14:00:00Z");
  assert.equal(runningFor("2026-11-01T14:00:00Z", t0 + 24_000), "for 24 s");
  assert.equal(runningFor("2026-11-01T14:00:00Z", t0 + 25_400), "for 25 s");
  assert.equal(runningFor("2026-11-01T14:00:00Z", t0 + 125_000), "for 2 min 05 s");
  assert.equal(runningFor("2026-11-01T14:00:00Z", t0 + 3_725_000), "for 1 h 2 min");
  assert.equal(runningFor("not a time", t0), "");
});

test("jobClock: stops when the strip has not been refreshed for a while", async () => {
  const { jobClock } = await import("../src/runspector/web/static/cards.js");
  const t0 = Date.parse("2026-11-01T14:00:00Z");
  assert.equal(jobClock("2026-11-01T14:00:00Z", " · expect ~50 s", t0 + 5_000, t0 + 30_000),
               "for 30 s · expect ~50 s");
  // the poll stopped (hidden tab): the job may long be done - the clock stands still
  assert.equal(jobClock("2026-11-01T14:00:00Z", "", t0 + 5_000, t0 + 3_600_000), null);
});
