// The planned week, seen and changed by the athlete (static/week.js, roadmap
// 19). Live use: three boxes named "Easy run 24 min", no way to see what was
// behind them, and no way to say "Friday, not Saturday".
import test from "node:test";
import assert from "node:assert/strict";
import { detailLine, dayLabel, dayChoices, dayPanelHtml } from "../src/runspector/web/static/week.js";

const EASY = {
  title: "Easy run 24 min", schedule_id: 16, workout_id: 4016, own: true,
  detail: { kind: "easy", what: "Easy run - slow enough to talk in full sentences", minutes: 24,
            km: 3.3, pace: "~7:21/km", steps: ["Run 24:00 min HR 135-153 bpm"], easier_min: null },
};
const VO2 = {
  title: "VO2max 5x4 min", schedule_id: 21, workout_id: 4021, own: true,
  detail: { kind: "vo2max", what: "VO2max intervals - hard, zone 5", minutes: 50, km: 8.4,
            pace: "~5:57/km", steps: ["Warm-up 12:00 min", "5× Run 4:00 min HR zone 5 + Easy jog 3:00 min"],
            easier_min: 30 },
};
const FOREIGN = { title: "Garmin Coach <b>run</b>", schedule_id: 31, workout_id: 9031, own: false,
                  detail: null };

test("detailLine: a box says what is behind it on hover", () => {
  assert.equal(detailLine(EASY),
               "Easy run - slow enough to talk in full sentences · 24 min · ~3.3 km · ~7:21/km");
  assert.match(detailLine(FOREIGN), /from your Garmin calendar/);
  assert.equal(detailLine(null), "");
});

test("dayLabel and dayChoices: the days a session can go to, by date", () => {
  assert.equal(dayLabel("2026-11-06"), "Fri 6 Nov");
  const days = dayChoices("2026-11-02", 15);
  assert.equal(days.length, 15);
  assert.deepEqual(days[0], { iso: "2026-11-02", label: "Mon 2 Nov" });
  assert.deepEqual(days[14], { iso: "2026-11-16", label: "Mon 16 Nov" });
});

test("dayPanelHtml: a day ahead shows the session and every change the athlete can make", () => {
  const html = dayPanelHtml({ day: "2026-11-07", planned: [EASY], done: [] },
                            { todayIso: "2026-11-02" });
  assert.match(html, /Sat 7 Nov/);
  assert.match(html, /slow enough to talk/);
  assert.match(html, /24 min · ~3.3 km · ~7:21\/km/);
  assert.match(html, /data-plan-move="16"/);
  assert.match(html, /<option value="2026-11-06">Fri 6 Nov<\/option>/);
  assert.doesNotMatch(html, /<option value="2026-11-07">/, "not the day it is on");
  assert.match(html, /data-plan-minutes="16"[^>]*value="24"/);
  assert.match(html, /data-plan-change="16"/);
  assert.match(html, /data-plan-remove="16"/);
  assert.doesNotMatch(html, /data-plan-add=/, "a day with a session offers no second one");
});

test("dayPanelHtml: a hard session shows its steps and its easier option", () => {
  const html = dayPanelHtml({ day: "2026-11-03", planned: [VO2], done: [] }, { todayIso: "2026-11-02" });
  assert.match(html, /5× Run 4:00 min HR zone 5/);
  assert.match(html, /easy run of 30 min instead/);
  assert.match(html, /<option value="vo2max" selected>/);
});

test("dayPanelHtml: a workout from the Garmin calendar can be taken out or replaced, not moved", () => {
  const html = dayPanelHtml({ day: "2026-11-04", planned: [FOREIGN], done: [] },
                            { todayIso: "2026-11-02" });
  assert.match(html, /Garmin Coach &lt;b&gt;run&lt;\/b&gt;/, "escaped");
  assert.match(html, /knows only its name/);
  assert.doesNotMatch(html, /data-plan-move=/);
  assert.match(html, /data-plan-change="31"[^>]*>Replace/);
  assert.match(html, /data-plan-remove="31"/);
});

test("dayPanelHtml: an empty day ahead offers a run; a day past and a run done offer nothing", () => {
  const empty = dayPanelHtml({ day: "2026-11-05", planned: [], done: [] }, { todayIso: "2026-11-02" });
  assert.match(empty, /data-plan-add="2026-11-05"/);
  const past = dayPanelHtml({ day: "2026-11-01", planned: [EASY], done: [] }, { todayIso: "2026-11-02" });
  assert.match(past, /has passed/);
  assert.doesNotMatch(past, /data-plan-(move|change|remove|add)=/);
  const ran = dayPanelHtml({ day: "2026-11-02", planned: [EASY],
                             done: [{ type: "running", name: "Morning run", training_load: 41 }] },
                           { todayIso: "2026-11-02" });
  assert.match(ran, /Morning run/);
  assert.match(ran, /run is done/);
  assert.doesNotMatch(ran, /data-plan-(move|change|remove|add)=/);
});

test("dayPanelHtml: the fields to add a run belong to their day", () => {
  // Round 2: picked on Thursday, they turned up on the next day opened.
  const html = dayPanelHtml({ day: "2026-11-05", planned: [], done: [] }, { todayIso: "2026-11-02" });
  assert.match(html, /data-plan-add-kind="2026-11-05"/);
  assert.match(html, /data-plan-add-minutes="2026-11-05"/);
});
