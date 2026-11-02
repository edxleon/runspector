/* The planned week, seen and changed by the athlete (roadmap 19).

   Live use: a week was planned - Mon, Tue, Sat - and the athlete saw it did
   not fit. The strip showed three boxes with a name each, nothing about what
   was behind them, and no way to say "Friday, not Saturday". A tap on a day
   opens it: what the session is, and the changes the athlete can make. A
   change is only a proposal - the server files it with the coach's word on it
   (`plan.propose_change`), and the calendar changes with the same "Put on
   watch" every proposal has.

   Pure string functions; `app.js` wires them, `web-tests/week.test.mjs` tests
   them. Everything from the server is escaped here. */
import { esc } from "./ui.js";

/** The kinds a session can be changed to - `planning.KINDS`. */
export const PLAN_KINDS = ["easy", "long", "threshold", "vo2max", "steady"];
const KIND_LABEL = { easy: "Easy run", long: "Long run", threshold: "Threshold",
                     vo2max: "VO2max intervals", steady: "Steady block" };
const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const asDate = iso => new Date(`${iso}T12:00:00Z`);
const isoOf = d => d.toISOString().slice(0, 10);

/** "Fri 6 Nov" - the day as the athlete names it, the date it means. */
export function dayLabel(iso) {
  const d = asDate(iso);
  return `${WD[d.getUTCDay()]} ${d.getUTCDate()} ${MON[d.getUTCMonth()]}`;
}

/** The days a session can go to: `n` days from today (the calendar window
 *  the app mirrors), as `{iso, label}`. */
export function dayChoices(todayIso, n = 15) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const d = asDate(todayIso);
    d.setUTCDate(d.getUTCDate() + i);
    out.push({ iso: isoOf(d), label: dayLabel(isoOf(d)) });
  }
  return out;
}

/** One line for a box's tooltip: what it is, how long, how far, how fast. */
export function detailLine(entry) {
  if (!entry) return "";
  const d = entry.detail;
  if (!d) return entry.title ? `${entry.title} - from your Garmin calendar` : "";
  return [d.what, `${d.minutes} min`, d.km ? `~${d.km} km` : "", d.pace || ""]
    .filter(Boolean).join(" · ");
}

const kindOptions = selected => PLAN_KINDS.map(k =>
  `<option value="${k}"${k === selected ? " selected" : ""}>${esc(KIND_LABEL[k])}</option>`).join("");

function entryHtml(e, { todayIso, canChange }) {
  const d = e.detail;
  const sid = esc(String(e.schedule_id ?? ""));
  const about = d
    ? `<div class="note">${esc(d.what)}</div>
       <div>${esc([`${d.minutes} min`, d.km ? `~${d.km} km` : "", d.pace || ""]
         .filter(Boolean).join(" · "))}</div>
       ${(d.steps || []).length > 1
         ? `<ol class="plan-steps">${d.steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol>` : ""}
       ${d.easier_min ? `<div class="note">Too tired on the day: an easy run of ${
         esc(String(d.easier_min))} min instead.</div>` : ""}`
    : `<div class="note">From your Garmin calendar - the app knows only its name. It can take it
         out, or put one of its own sessions in its place.</div>`;
  if (!canChange || e.schedule_id == null) {
    return `<div class="plan-entry"><b>${esc(e.title || "Session")}</b>${about}</div>`;
  }
  const move = e.own
    ? `<label>Move to <select data-plan-move="${sid}"><option value="">a day…</option>${
        dayChoices(todayIso).filter(c => c.iso !== e.day).map(c =>
          `<option value="${c.iso}">${esc(c.label)}</option>`).join("")}</select></label>`
    : "";
  return `<div class="plan-entry" data-plan-entry="${sid}">
    <b>${esc(e.title || "Session")}</b>${about}
    <div class="plan-actions">
      ${move}
      <label>Minutes <input type="number" min="10" max="300" step="5" inputmode="numeric"
        data-plan-minutes="${sid}" value="${esc(String(d?.minutes ?? 30))}"></label>
      <label>Kind <select data-plan-kind="${sid}">${kindOptions(d?.kind || "easy")}</select></label>
      <button type="button" class="btn btn-quiet" data-plan-change="${sid}">${
        e.own ? "Change" : "Replace"}</button>
      <button type="button" class="btn btn-quiet" data-plan-remove="${sid}">Take out</button>
    </div>
  </div>`;
}

/** The panel under the strip for one day: what is planned and run there,
 *  and - for a day ahead - the changes the athlete can make. A change files a
 *  proposal; nothing here writes to Garmin. */
export function dayPanelHtml(cell, { todayIso }) {
  const day = cell.day;
  const past = day < todayIso;
  const ran = (cell.done || []).filter(g => /running/i.test(g.type || ""));
  const doneToday = day === todayIso && ran.length > 0;
  const canChange = !past && !doneToday;
  const runs = ran.map(r => `<div class="plan-entry"><b>Run: ${esc(r.name || "Run")}</b>${
    r.training_load != null ? ` <span class="note">· load ${esc(String(r.training_load))}</span>` : ""}</div>`)
    .join("");
  const planned = (cell.planned || []).map(e => entryHtml({ ...e, day }, { todayIso, canChange }))
    .join("");
  const add = canChange && !(cell.planned || []).length
    ? `<div class="plan-actions plan-add"><span>Add a run:</span>
         <select data-plan-add-kind="${esc(day)}" aria-label="Kind">${kindOptions("easy")}</select>
         <label>Minutes <input type="number" min="10" max="300" step="5" inputmode="numeric"
           data-plan-add-minutes="${esc(day)}" value="30"></label>
         <button type="button" class="btn btn-quiet" data-plan-add="${esc(day)}">Add</button></div>`
    : "";
  const why = past ? `<div class="note">This day has passed - change what lies ahead.</div>`
    : doneToday ? `<div class="note">Today's run is done - change what lies ahead.</div>`
    : !(cell.planned || []).length && !ran.length ? `<div class="note">Nothing planned.</div>` : "";
  return `<div class="plan-day" data-plan-panel="${esc(day)}">
    <div class="plan-head"><b>${esc(dayLabel(day))}</b>
      <button type="button" class="linkish" data-plan-close aria-label="Close">Close</button></div>
    ${runs}${planned}${why}${add}
    ${canChange ? `<div class="note">A change is a proposal: the coach says what it thinks of it,
      and your calendar changes only with "Put on watch".</div>` : ""}
  </div>`;
}

/** The coach's answer to a change asked in words under the strip - shown
 *  there, where it was asked, with the change's own button for the watch
 *  (`proposalHtml`, cards.js `renderProposal`). Live use: "Ask" started a job
 *  and its "Put on watch" sat in a card on the Coach tab, under a job the
 *  athlete had to go and find. `job` and `card` are from /api/state. */
export function askedHtml(job, card, proposalHtml = "") {
  const close = `<button type="button" class="linkish" data-plan-asked-close>Close</button>`;
  if (card) {
    return `<div class="plan-asked">
      <div class="plan-head"><span class="subhead">The coach on your change</span>${close}</div>
      ${card.question ? `<div class="note">You asked: ${esc(card.question)}</div>` : ""}
      <b>${esc(card.headline || "")}</b>
      ${card.verdict ? `<div>${esc(card.verdict)}</div>` : ""}
      ${proposalHtml}
      <button type="button" class="linkish" data-plan-asked-open>The whole answer in the Coach tab →</button>
    </div>`;
  }
  if (!job) return "";
  if (job.status === "queued" || job.status === "running") {
    // Live use: a move asked in words "ran" ten minutes - it had waited for the
    // athlete's Claude quota, and this said only "working".
    if (/quota/i.test(String(job.note || ""))) {
      return `<div class="plan-asked plan-working"><span class="busy-ring" aria-hidden="true"></span>
        Waiting for your Claude quota - the coach starts by itself once it is back. A move or
        another length needs no coach: tap the day in the strip.</div>`;
    }
    return `<div class="plan-asked plan-working"><span class="busy-ring" aria-hidden="true"></span>
      The coach is working on your change - its answer appears here, with the button for the
      watch.</div>`;
  }
  if (job.status !== "done") {
    return `<div class="plan-asked"><div class="plan-head"><span class="note proposal-warn">The coach
      could not answer: ${esc(job.result_summary || job.note || job.status)}</span>${close}</div></div>`;
  }
  return "";
}

/** The seven days the strip shows: this week (0) or the next (1) - a week
 *  planned on Sunday is next week's (iter). Without next week's data, this
 *  week. */
export function stripDays(plan, which) {
  const next = (plan || {}).next || [];
  return which === 1 && next.length ? next : ((plan || {}).days || []);
}
