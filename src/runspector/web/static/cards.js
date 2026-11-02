/* ═══════════════════════════════════════════════════════════════════════════
   Coach cards + job strip — ONE renderer, ONE poller.

   Two layers in one file, deliberately kept apart:
   · PURE LOGIC (top) — computes without a DOM and is tested under Node
     (web-tests/cards.test.mjs): meta line, average duration, parent/child
     grouping, 409 classification, today's card, truncation.
   · DOM (bottom) — renderCard / mountCards / mountJobs / jobPoller.

   Invariants that are NOT negotiable here:
   · Everything from a card or a job is model text or foreign text and goes
     through `esc()`. No Markdown parser — `**` and `#` are removed, not
     interpreted.
   · Feedback and follow-up questions belong to every card: each one comes
     from a job the user started (the server writes `source = "job"`).

   Import is relative (`./ui.js`), not `/static/ui.js`: Node does not know the
   server root, and this module is imported by tests.
   ═══════════════════════════════════════════════════════════════════════════ */

import { esc, fmtAgo, fmtDay, toast, apiGet, apiPost, armOrFire,
         resetArm, rearmIn } from "./ui.js";
import { plainJobError } from "./logic.js";

/* ═══ Pure logic ═══════════════════════════════════════════════════════════ */

export const ACTIVE_STATUS = ["queued", "running"];
export const isActive = j => ACTIVE_STATUS.includes(j?.status);
export const cardId = c => String(c?.id || c?.job_id || "");

/** Text cut to `max` characters with "…" at the end — without half a word at
 *  the edge. */
export function truncate(t, max = 400) {
  const s = String(t ?? "");
  if (s.length <= max) return s;
  const short = s.slice(0, Math.max(1, max - 1));
  const cut = short.lastIndexOf(" ");
  return (cut > max * 0.6 ? short.slice(0, cut) : short).trimEnd() + "…";
}

/** "claude-opus-5" → "opus-5"; a date suffix ("-20260901") is dropped. */
export function modelShort(m) {
  return String(m || "").replace(/^claude-/, "").replace(/-\d{8}$/, "");
}

/** Building blocks of the meta line, in display order. `fmt` is injectable so
 *  that the test can check the structure instead of a locale's output.
 *  Cards without the runner's stamps fall back to `day`/`written` — they then
 *  show less, but nothing wrong. */
export function metaParts(c, { day = fmtDay, ago = fmtAgo } = {}) {
  const t = [];
  if (c.data_through) t.push("data up to " + day(c.data_through));
  else if (c.day) t.push(day(c.day));
  if (c.generated_at) t.push("generated " + ago(c.generated_at));
  else if (c.written) t.push("generated " + ago(c.written));
  // The model's name is in "How this card was made", not on every card: to a
  // reader it was one more word without a meaning.
  // `cost_usd` stays in the card file and is deliberately not shown: on a
  // subscription the CLI's figure is nominal, and a price on every card makes
  // it the thing the eye compares. See the note above `tplButton` in app.js.
  return t;
}

/** Average duration of finished runs in seconds (finished − started); null
 *  without a basis. */
export function avgDurationSec(jobs) {
  const d = (jobs || [])
    .filter(j => j.status === "done" && j.started && j.finished)
    .map(j => (new Date(j.finished) - new Date(j.started)) / 1000)
    .filter(s => Number.isFinite(s) && s > 0);
  return d.length ? d.reduce((a, b) => a + b, 0) / d.length : null;
}

/** "for 24 s" / "for 3 min" - how long an active job has been running, at
 *  `now`. The strip ticks it every second (`tickJobClocks`); the poll alone
 *  moved it in 8-second jumps. */
export function runningFor(startedIso, now = Date.now()) {
  const s = Math.max(0, (now - new Date(startedIso).getTime()) / 1000);
  if (!Number.isFinite(s)) return "";
  if (s < 90) return `for ${Math.floor(s)} s`;
  if (s < 3600) return `for ${Math.floor(s / 60)} min ${String(Math.floor(s % 60)).padStart(2, "0")} s`;
  return `for ${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

/** "~40 s" / "~2 min" — an expectation, not a clock. */
export function fmtExpectation(sec) {
  if (sec == null || !Number.isFinite(sec)) return "";
  if (sec < 90) return `~${Math.max(10, Math.round(sec / 10) * 10)} s`;
  return `~${Math.round(sec / 60)} min`;
}

/** Parent/child: answer cards (`parent`) hang below their parent card, orphans
 *  (parent not in the list) stay visible in the main list. Children in
 *  chronological order (oldest first — a conversation is read forwards), the
 *  main list keeps the order of the source (newest first). */
export function groupCards(cards) {
  const all = (cards || []).filter(c => cardId(c));
  const ids = new Set(all.map(cardId));
  const main = [], children = new Map();
  for (const c of all) {
    const p = c.parent ? String(c.parent) : "";
    if (p && p !== cardId(c) && ids.has(p)) {
      if (!children.has(p)) children.set(p, []);
      children.get(p).push(c);
    } else main.push(c);
  }
  for (const k of children.values()) k.reverse();
  return { main, children };
}

/** The 409 answer of /api/spawn has three faces. The switch is the `code`
 *  field of the response — never the message text, which is free to change:
 *  `already_running` (ref = job id) → just show it; `card_current` (ref = card
 *  id) → offer "recompute", which repeats the request with force:true;
 *  `queue_full` → just show it. Accepts the thrown error or a plain object. */
export function classify409(err) {
  const code = String(err?.code || err?.data?.code || "");
  const ref = err?.ref || err?.data?.ref || null;
  if (code === "already_running") return { type: "running", ref };
  if (code === "card_current") return { type: "current", ref };
  if (code === "queue_full") return { type: "queue_full", ref };
  return { type: "other", ref };
}

/** Newest card of one kind (main list, no answers) — and whether it is from
 *  today. `today` is an ISO day (local!), compared against `day`. */
export function todayCard(cards, today, kind = "train-today", saidAt = null) {
  const k = (cards || [])
    // An answer in a conversation is not the day's card - unless it was started
    // as a next step: "Should I train today?" from a card is that day's card.
    .filter(c => c && c.kind === kind && (!c.parent || c.via === "next"))
    .sort((a, b) => String(b.day || "").localeCompare(String(a.day || ""))
               || String(b.generated_at || b.written || "")
                    .localeCompare(String(a.generated_at || a.written || "")));
  if (!k.length) return null;
  const current = k[0].day === today;
  // Today's card, made before the athlete said they are ill (or that it is
  // over): it argues for a call the check-in has since changed.
  const outdated = current && !!saidAt && String(k[0].snapshot_generated_at || "") < String(saidAt);
  return { card: k[0], current, outdated };
}

/** Up to `n` points visible, the rest behind a disclosure. Default 2: with
 *  five open bullets of 400 characters each, the newest card was ~1400 px tall
 *  on a phone — one found the verdict, but never the end. */
export function splitBullets(bullets, n = 2) {
  const b = Array.isArray(bullets) ? bullets.filter(x => typeof x === "string" && x) : [];
  return { visible: b.slice(0, n), rest: b.slice(n) };
}

/** The points with where each comes from (`sources`, index for index, from
 *  `evidence.split_marks`). Paired BEFORE empty points are dropped, so a
 *  dropped point never hands its mark to the next one. */
export function bulletItems(c) {
  const src = Array.isArray(c?.sources) ? c.sources : [];
  return (Array.isArray(c?.bullets) ? c.bullets : [])
    .map((text, i) => ({ text, src: src[i] || null }))
    .filter(x => typeof x.text === "string" && x.text);
}

/** The three places a point can come from, in the words the page uses. */
export const SOURCES = {
  measured: { label: "your data", tip: "Read from your Garmin data through the app's own tools" },
  you: { label: "you said", tip: "From what you told the app: your note, the You tab or a check-in" },
  derived: { label: "coach's reading", tip: "Worked out by the coach from your numbers and the rules" },
};

/** The badge of one point, or null. `warn` when the point says it is your data
 *  and the record does not back that; `why` then says what is missing, in a
 *  sentence the page shows under the point (a tooltip never reaches a phone). */
export function sourceBadge(s) {
  const k = s && SOURCES[s.kind];
  if (!k) return null;
  const why = [];
  if (s.kind === "measured") {
    if (s.called === false) why.push(`it names ${s.ref || "a source"}, which the coach did not look at`);
    if (s.no_data) why.push("none of its numbers is in the data the coach read");
    else if (s.unmatched?.length) {
      const one = s.unmatched.length === 1;
      why.push(`${s.unmatched.join(", ")} ${one ? "is" : "are"} not in the data the coach read - `
               + "probably worked out by the coach");
    }
  }
  return { kind: s.kind, label: k.label, warn: why.length > 0,
           tip: s.from ? `${k.tip}: ${s.from}` : k.tip,
           why: why.length ? `Marked as your data, but ${why.join("; ")}.` : "" };
}

/** The one line beside "How this card was made". */
export function evidenceSummary(ev) {
  if (!ev) return "";
  if (!ev.recorded) return "not recorded for this card";
  const n = Number(ev.reads) || 0;
  const looked = `looked at ${n} thing${n === 1 ? "" : "s"}`;
  // A record that is not the whole session checks nothing (`evidence.whole`).
  if (ev.checked === false) return `${looked} · record incomplete, numbers not checked`;
  // `missing` counts every number found nowhere; the list holds only the first.
  const miss = Number(ev.missing ?? (ev.unmatched || []).length) || 0, total = Number(ev.numbers) || 0;
  const nums = !total ? "" : miss ? `${total - miss} of ${total} numbers found in your data`
    : `all ${total} numbers found in your data`;
  return [looked, nums].filter(Boolean).join(" · ");
}

/** What a toggle of "How this card was made" does with the card's record:
 *  "load" when it is opened and was never tried, "forget" a failed load when
 *  it is closed - so the next opening tries again - else nothing. Never "load"
 *  on a failure: a re-render inserts the section already open, the browser
 *  fires `toggle` for it, and loading there looped without end after one
 *  failed request (fetch, fail, redraw, toggle, fetch). */
export function evidenceToggle(open, cached) {
  if (open) return cached === undefined ? "load" : null;
  return cached && typeof cached === "object" && cached.error ? "forget" : null;
}

/** What a "How this card was made" section holds: its record whenever one
 *  is loaded, open or closed. Drawn only into an open section, it was gone
 *  after the section was closed and the page drawn again (the job poll, an
 *  Apply) - and opening it again loaded nothing, the record being cached, so
 *  the section stayed empty until a reload. */
export function evidenceBody(cached, det = () => "") {
  return cached === undefined ? "" : renderEvidence(cached, det);
}

/** A step's arguments in words: `{"period_days": 84}` → "last 84 days". What
 *  is not known stays as it came, short. */
export function argsWords(args) {
  if (!args) return "";
  let a;
  try { a = typeof args === "string" ? JSON.parse(args) : args; } catch { return String(args).slice(0, 80); }
  if (!a || typeof a !== "object") return String(args).slice(0, 80);
  return Object.entries(a).map(([k, v]) =>
    /^(period_)?days$/.test(k) ? `last ${v} days`
    : k === "day" ? String(v)
    : k === "activity_id" ? "one run"
    : `${k.replace(/_/g, " ")} ${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ").slice(0, 120);
}

/** "the headline", "point 2" - where a number sits, for the sentence. */
const whereWords = w => (w === "headline" || w === "verdict" ? `the ${w}` : w);

/** What the coach looked at for one card (`/api/cards/<id>/evidence`), as
 *  HTML. `rec` is the record, "loading", or {error}. Every value in it is tool
 *  output or model input and goes through `esc()`. `det(name)` keeps nested
 *  disclosures open across a re-render. */
export function renderEvidence(rec, det = () => "") {
  if (!rec || rec === "loading") return `<div class="note">Loading…</div>`;
  if (rec.error) return `<div class="note">The record could not be loaded: ${esc(rec.error)}</div>`;
  const intro = `<p class="note">The coach can reach your data only through this app's own tools
    - no web, no files, nothing else on this computer. This is everything it looked at for this
    card, in order, with what came back. A point on the card comes from your data unless it is
    marked "you said" or "coach's reading".</p>`;
  if (!rec.recorded) {
    return `<p class="note">${rec.gone ? "The record of this card is no longer there - its file was deleted."
      : rec.old ? "This card was made before the app kept a record of what the coach looked at."
      : "The app could not record what the coach looked at for this card."}</p>`;
  }
  const ck = rec.check || {}, listed = ck.unmatched || [], total = Number(ck.numbers) || 0;
  const missing = Number(ck.missing ?? listed.length) || 0;
  const more = missing - listed.length;
  // The count is on the summary line ("52 of 53 numbers found in your data");
  // here only what was not found - said once, not twice.
  const check = ck.checked === false
    ? `<div class="ev-check ev-warn">This record is incomplete - the run's output was too long to
        keep whole - so the numbers on this card are not checked against it.</div>`
    : missing
      ? `<div class="ev-check ev-warn">Not found in your data: ${listed.map(u =>
            `<b>${esc(u.value)}</b> (${esc(whereWords(u.where))})`).join(", ")}${
          more > 0 ? ` and ${esc(more)} more` : ""} - probably worked out by the coach, or not
          backed by your data.</div>`
      : "";
  const honest = total && ck.checked !== false ? `<p class="note">Found means the number was in front of the coach - not
    that it was read rightly. That part is yours to judge.</p>` : "";
  // A step in words - "Read your time in heart-rate zones · last 84 days"; the
  // tool's own name only as a tooltip, it said the label a second time.
  const calls = (rec.calls || []).map((c, i) => `<li class="ev-call${c.error ? " ev-failed" : ""}">
      <span class="ev-label" title="${esc(c.tool || "")}">${esc(c.changed ? c.label : "Read " + c.label)}${
        argsWords(c.args) ? ` · ${esc(argsWords(c.args))}` : ""}${c.error ? " - failed" : ""}</span>
      <details class="card-more ev-result" ${det("ev" + i)}><summary>What came back</summary>
        <pre class="ev-pre mono">${esc(c.result || "(nothing)")}</pre></details></li>`).join("");
  const list = calls ? `<ol class="ev-calls">${calls}</ol>`
    : `<div class="note">It looked at nothing.</div>`;
  const inp = rec.inputs || {};
  const given = [["What the app knows about you", inp.about_you], ["Your note", inp.your_note],
                 ["What you told the coach", inp.told], ["The task", inp.task]].filter(([, v]) => v)
    .map(([k, v]) => `<div class="ev-given"><div class="ev-label">${esc(k)}</div>
       <pre class="ev-pre mono">${esc(v)}</pre></div>`).join("");
  const givenBox = given ? `<details class="card-more" ${det("evgiven")}><summary>What the coach
    was given besides the rules</summary>${given}</details>` : "";
  const foot = [modelShort(rec.model), inp.rules_sha256 ? `rules ${inp.rules_sha256.slice(0, 8)}` : "",
                inp.app_version ? `app ${inp.app_version}` : "",
                rec.complete === false ? "the record is incomplete" : "",
                "kept with this card, deleted with it"].filter(Boolean);
  return `<div class="ev">${intro}${check}${honest}${list}${givenBox}
    <div class="card-metaline">${foot.map(esc).join(" · ")}</div></div>`;
}

/** Full text for display: line breaks stay, markup goes. NO Markdown parser —
 *  the text is escaped afterwards and placed into a `white-space: pre-wrap`
 *  element.
 *
 *  Also covers chat-style Markdown (bold with ONE asterisk, italics with
 *  underscores, bare code fences). A pair is only removed if it sits on the
 *  SAME line and encloses non-empty text: a single asterisk (multiplication,
 *  footnote) and `snake_case` stay untouched. */
export function textPlain(t) {
  return String(t ?? "")
    .replace(/\r\n?/g, "\n")
    .replace(/^ *```.*$/gm, "")
    .replace(/\*\*(\S(?:[^*\n]*\S)?)\*\*/g, "$1")
    .replace(/\*\*/g, "")
    .replace(/\*(\S(?:[^*\n]*\S)?)\*/g, "$1")
    .replace(/(^|[\s(])_(\S(?:[^_\n]*\S)?)_(?=$|[\s.,;:!?)])/gm, "$1$2")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** What belongs in the job strip: everything active, plus failures and
 *  cancellations of the last 24 h (with their reason) — and on request
 *  (`finished`) the completed runs of the last 24 h as well. Filtering on
 *  queued/running alone lets a run that died while waiting for quota vanish
 *  without a trace. */
export function jobsForStrip(jobs, { now = Date.now(), finished = false, max = 8 } = {}) {
  const limit = now - 24 * 3600e3;
  const recent = j => {
    const t = new Date(j.finished || j.started || j.created || 0).getTime();
    return Number.isFinite(t) && t >= limit;
  };
  return (jobs || []).filter(j => j && j.id && (
    isActive(j)
    || (j.status !== "done" && recent(j))
    || (finished && j.status === "done" && recent(j))
  )).slice(0, max);
}

/* ═══ DOM ══════════════════════════════════════════════════════════════════ */

const JOB_LABEL = { queued: "queued", running: "running", done: "done",
                    failed: "failed", timeout: "timeout", cancelled: "cancelled" };
const JOB_PILL = { queued: "pill-unknown", running: "pill-warn", done: "pill-ok",
                   failed: "pill-down", timeout: "pill-down", cancelled: "pill-stale" };

const openCards = new Set();         // expanded card ids, page-wide
const seen = new Map();              // container key → Set(ids) for auto-expand
const filterKind = new Map();        // container key → chosen kind
const state = new WeakMap();         // container element → {cards, opts}
const jobsState = new WeakMap();     // container element → {jobs, opts}
const followUps = new Map();         // card id → {job_id, ts}
const jobLogs = new Map();           // job id → lines[] of the open log
// card id → its record, "loading" or {error}. Exported for the tests: whether
// a closed section holds a loaded record is renderCard's to get right.
export const evidenceCache = new Map();
// Expanded <details> survive the re-render: the job poll rebuilds every 8 s,
// and "3 more points" would snap shut each time — unusable while reading a
// long analysis. Key "<id>|<name>".
const openDetails = new Set();
let context = {};                    // page-wide callbacks (setCardContext)
let installed = false;

/** Callbacks of the page: `onJobStart(r)` after a follow-up question was
 *  started (pull the jobs, start the poller). Applies to all containers that
 *  carry no `onJobStart` of their own in their opts. */
export function setCardContext(k) { context = { ...context, ...k }; }

function elOf(x) { return typeof x === "string" ? document.querySelector(x) : x; }
function keyOf(el) { return el.dataset.cardsKey || el.id || "_"; }

/* ── Card ────────────────────────────────────────────────────────────────── */

/** One card as HTML. `opts`: kindTitle {kind→title}, defaultTitle, children
 *  [answer cards], feedback/followUp (false = hide), open (overrides the
 *  remembered state), noRef, refText(c). Needs NO DOM — a pure string
 *  function, which is what makes the escaping invariant testable. */
export function renderCard(c, opts = {}) {
  const id = cardId(c);
  const open = opts.open ?? openCards.has(id);
  const title = opts.forceTitle || (opts.kindTitle || {})[c.kind] || opts.defaultTitle || "Analysis";
  const children = opts.children || [];
  const meta = metaParts(c);
  const p = followUps.get(id);
  if (p && (children.some(k => cardId(k) === p.job_id || String(k.job_id || "") === p.job_id)
            || Date.now() - p.ts > 45 * 60e3)) {
    followUps.delete(id);
  }

  // A readable reference instead of a raw one: `opts.refText(c)` may turn an
  // activity id into "Threshold run, Mon 7 Sep". Without a resolver the raw
  // value stays — but a bare number like "9000000042" tells the user nothing.
  const refTxt = opts.noRef ? "" : String((opts.refText ? opts.refText(c) : c.ref) || "");
  const det = name => {
    const k = `${id}|${name}`;
    return `data-det="${esc(k)}"${openDetails.has(k) ? " open" : ""}`;
  };
  // An answer in a conversation says what it answers - the athlete's own question.
  const asked = c.question
    ? `<span class="card-question">You asked: ${esc(truncate(String(c.question), 300))}</span>` : "";
  const head = `<button type="button" class="card-head" data-card="${esc(id)}" aria-expanded="${open}">
      ${asked}<span class="card-meta">${esc(title)}${refTxt ? " · " + esc(refTxt) : ""}${
        meta[0] ? " · " + esc(meta[0]) : ""}</span>
      <h3>${esc(textPlain(c.headline) || "(no headline)")}</h3>
      ${c.verdict ? `<span class="card-verdict">${esc(textPlain(c.verdict))}</span>` : ""}
      <span class="chev" aria-hidden="true">${open ? "▴" : "▾"}</span>
    </button>`;

  let body = "";
  if (open) {
    const items = bulletItems(c);
    const visible = items.slice(0, 2), rest = items.slice(2);
    // `textPlain` FIRST, then truncate, then escape. The model answers in chat
    // Markdown often enough ("**Sessions:** 3 today"), and without this the
    // asterisks and fences were rendered literally - the function existed and
    // was fully tested, but nothing called it. `card-text` keeps the line
    // breaks that `textPlain` preserves.
    //
    // The HEADLINE, the verdict and the delta go through it too: wiring only
    // the bullets left the most prominent line on the card - the one in the
    // <h3> - showing `**Bold** headline` verbatim.
    //
    // Each point carries where it comes from: a small badge in front, and,
    // when a point says it is your data and the record does not back that,
    // the reason in a line under it.
    // Only the exceptions are marked - "you said", "coach's reading": "your
    // data" in front of nearly every point said nothing, and a point without
    // a mark is data. Whether its numbers are in the data is the check in
    // "How this card was made", not a line in the middle of the reading.
    const points = list => list.map(({ text, src }) => {
      const b = sourceBadge(src);
      const badge = b && b.kind !== "measured"
        ? `<span class="src src-${esc(b.kind)}" title="${esc(b.tip)}">${esc(b.label)}</span> ` : "";
      return `<p class="card-text">${badge}${esc(truncate(textPlain(text), 700))}</p>`;
    }).join("");
    // The further points and what changed since the last card behind ONE line:
    // each its own fold-out made four of them on a card.
    const delta = c.delta ? `<div class="card-delta">${esc(textPlain(c.delta))}</div>` : "";
    const moreLabel = [rest.length ? `${rest.length} more point${rest.length === 1 ? "" : "s"}` : "",
                       delta ? (rest.length ? "what changed since the last card" : "What changed since the last card") : ""]
      .filter(Boolean).join(" · ");
    const content = points(visible) + (moreLabel
      ? `<details class="card-more" ${det("more")}><summary>${moreLabel}</summary>${points(rest)}${delta}</details>`
      : "");
    // The head already shows the first part ("data up to ..."): not again here.
    const tail = meta.slice(1);
    const metaLine = tail.length ? `<div class="card-metaline">${tail.map(esc).join(" · ")}</div>` : "";
    // What the coach looked at - loaded when opened (`install()`), from the
    // card's own record. Cards older than the record have none.
    const evidence = c.evidence
      ? `<details class="card-more card-evidence" ${det("evidence")} data-evidence="${esc(id)}">
           <summary>How this card was made · ${esc(evidenceSummary(c.evidence))}</summary>${
           evidenceBody(evidenceCache.get(id), n => det("evidence-" + n))}</details>`
      : "";
    const proposal = renderProposal(c.proposal, opts);
    const fb = opts.feedback !== false ? renderFeedback(id, c.feedback) : "";
    const value = c.feedback?.value;
    const respond = fb
      ? `<details class="card-more card-respond" ${det("respond")}${value ? " open" : ""}>
           <summary>Feedback${value ? (value === "good" ? " · 👍" : " · 👎") : ""}</summary>${fb}</details>` : "";
    body = `<div class="card-body">${content || `<div class="empty-note">No content.</div>`}${
      proposal}${renderCheckIn(c.check_in)}${renderNext(id, c.next, c.parent || id)}${evidence}${metaLine}${respond}</div>`;
  }
  // The conversation's question field stands under the whole thread - the
  // card and every answer in it (roadmap 25) - not in the feedback, and only
  // on the card the thread hangs on.
  const pending = followUps.get(id);
  // A card shown on its own - also an answer whose card is gone - holds the field.
  const ask = opts.followUp !== false && (open || children.length)
    ? renderFollowUp(id, pending, children.length) : "";
  // Answers hang visibly BELOW the parent card — even when it is collapsed,
  // otherwise nobody would see that a follow-up question has been answered.
  const childrenHtml = children.length
    ? `<div class="card-children">${children.map(k =>
        renderCard(k, { ...opts, children: [], forceTitle: k.via === "next" ? undefined : "Answer",
                        noRef: true, open: undefined, followUp: false })).join("")}</div>`
    : "";
  // What the card kept stands outside the head and its fold: never kept
  // without being seen, open or closed (roadmap 25).
  const told = renderTold(c.told, c.told_full);
  return `<article class="coach-card${c.parent ? " card-child" : ""}"
             data-card-id="${esc(id)}">${head}${told ? `<div class="card-told-wrap">${told}</div>` : ""}${
             body}${childrenHtml}${ask}</article>`;
}

/** The session the coach filed, and the ONE button that writes to Garmin.
 *  `p.preview` is text from `planning.describe` (built from the athlete's own
 *  zones, not from the model) and is escaped like everything else on the card.
 *  The button is the human's yes: two taps, then `POST /api/plan/apply`.
 *  `opts.canApply === false` (demo, or no Garmin session) keeps it disabled
 *  with the reason next to it, so a dead button never has to be explained. */
//: Long enough for a whole week of Garmin round trips on a slow line.
const APPLY_TIMEOUT_MS = 180_000;


export function renderProposal(p, opts = {}) {
  if (!p || !p.id) return "";
  // "applied" is not the same as "finished": a package whose third upload
  // failed is applied AND has sessions waiting. Showing the pill there took
  // away the only button that could add them.
  const pending = Number(p.pending || 0);
  // An old day Garmin would not take off (nothing happened): another try.
  const retryOff = Number(p.retry_off || 0);
  const applied = p.status === "applied" && pending === 0 && !retryOff;
  const partial = p.status === "applied" && (pending > 0 || retryOff > 0);
  const can = opts.canApply !== false;
  const warn = (p.warnings || []).map(w => `<div class="note proposal-warn">! ${esc(w)}</div>`).join("");
  const ids = Array.isArray(p.workouts) ? p.workouts : [];
  const week = Number(p.days) > 1;
  // The athlete's own change to the week (roadmap 19): the coach's word on it
  // stands next to the button, once - not again among the steps - and the
  // buttons say what they do to the calendar.
  const change = p.change === true;
  const removing = change && !Number(p.days);
  // Who filed it: the athlete in the week strip, or the coach from their words
  // - never called the athlete's own when it was not (security review).
  const byCoach = change && p.filed_by === "coach";
  // The way back exists as soon as anything is on Garmin, not only once all
  // of it is: a half-applied week used to offer "add the rest" and no undo.
  const undo = `<button type="button" class="btn btn-quiet" data-undo="${esc(p.id)}"
               data-arm-label="Sure? ${change ? "Your calendar goes back" : "It comes off the calendar"}"${
                 can ? "" : " disabled"}
               title="Take it off the Garmin calendar - the workout stays in your library">
         ${change ? "Undo the change" : "Take off watch"}</button>`;
  const label = partial && !pending ? "Take the old day off"
    : partial ? `Put the remaining ${esc(pending)} on watch`
    : removing ? "Take it off the watch" : change ? "Put the change on watch" : "Put on watch";
  // A change half done - the old day off, the new session not up - can be
  // tried again AND taken back (review: it offered no way back).
  const tookOff = change && Number(p.taken_off || 0) > 0;
  const button = applied
    ? `<span class="pill pill-ok">${change ? "Changed on Garmin" : `On Garmin${ids.length > 1
        ? ` · ${ids.length} workouts` : ids.length ? ` · workout ${esc(ids[0])}` : ""}${
        p.day ? ` · ${esc(p.day)}` : ""}`}</span>
       ${undo}`
    : `<button type="button" class="btn btn-primary" data-apply="${esc(p.id)}"
               data-arm-label="Sure? ${partial ? `${esc(pending)} more go` : change
                 ? "Your Garmin calendar changes" : week ? "The whole week goes" : "It goes"}${
                 change && !partial ? "" : " on the watch"}"${can ? "" : " disabled"}
               title="${change ? "Change your Garmin calendar as listed" : `Upload to Garmin, schedule ${
                 week ? `${esc(p.days)} sessions from` : "for"} ${esc(p.day || "the day")}, push to the watch`}">
         ${label}</button>${
           (partial && (ids.length || retryOff)) || tookOff ? undo : ""}${
           can ? "" : `<span class="note">${
             opts.applyHint ? esc(opts.applyHint) : "needs a Garmin login"}</span>`}`;
  // The safety net, next to the button that writes: what changes, and that
  // it can be taken back. Said only while there is something left to write.
  const net = applied ? "" : change
    ? `<div class="note proposal-net">Only what is listed above changes on your Garmin calendar.
       "Undo the change" puts it back as it was; workouts stay in your Garmin library.</div>`
    : `<div class="note proposal-net">Only what is listed above goes to
    Garmin - nothing else on your calendar changes. "Take off watch" removes it again; the
    workout stays in your Garmin library.</div>`;
  const notes = (Array.isArray(p.notes) ? p.notes : []).filter(Boolean);
  const yours = byCoach
    ? `<p class="note">Filed by the coach from what you said - check what it takes off and puts
        on before it goes on the watch.</p>`
    : `<p class="note">It is your call - the change stands as you made it.</p>`;
  const coach = !change ? ""
    : notes.length
      ? `<div class="proposal-coach"><div class="subhead">The coach</div>${
          notes.map(n => `<p class="note">${esc(n)}</p>`).join("")}${yours}</div>`
      : `<div class="proposal-coach"><p class="note">The coach has nothing against it.</p>${
          byCoach ? yours : ""}</div>`;
  const steps = change
    ? String(p.preview || "").split("\n").filter(l => !l.startsWith("  coach: ")).join("\n")
    : String(p.preview || "");
  return `<div class="card-proposal" data-proposal="${esc(p.id)}">
    <div class="subhead">${byCoach ? "The coach's change, from your words" : change ? "Your change" : week ? `Proposed week · ${esc(p.days)} sessions`
      : "Proposed session"}${p.day && !change ? ` · ${esc(p.day)}` : ""}${
      partial ? ` · ${esc(Number(p.days || 1) - pending)} of ${esc(p.days)} on Garmin` : ""}</div>
    ${change && Array.isArray(p.lines) && p.lines.length
      ? `<ul class="change-lines">${p.lines.map(l => `<li>${esc(l)}</li>`).join("")}</ul>
         <details class="card-more"><summary>What goes to Garmin</summary><pre class="proposal-steps">${
           esc(steps)}</pre></details>`
      : `<pre class="proposal-steps">${esc(steps)}</pre>`}
    ${coach}
    ${warn}
    <div class="btn-row">${button}</div>
    ${net}
  </div>`;
}

async function undoProposal(id, btn) {
  if (!id) return;
  const box = btn?.closest("[data-proposal]");
  box?.querySelectorAll("button").forEach(b => (b.disabled = true));
  try {
    const r = await apiPost("/api/plan/undo", { proposal_id: id }, APPLY_TIMEOUT_MS);
    toast(String(r?.result || "Taken off the calendar."), { ms: 8000 });
    context.onProposal?.(id, { undone: true });
  } catch (e) {
    if (/timed out|timeout/i.test(String(e.message || ""))) {
      toast("Still talking to Garmin - refresh in a moment to see what changed.",
            { warn: true, ms: 10000 });
      return;
    }
    box?.querySelectorAll("button").forEach(b => (b.disabled = false));
    if (e.message === "403") return;
    toast("Not taken off: " + (e.message || "server gone"), { warn: true, ms: 8000 });
    return;
  }
  context.onJobStart?.();
}


async function applyProposal(id, btn) {
  if (!id) return;
  const box = btn?.closest("[data-proposal]");
  box?.querySelectorAll("button").forEach(b => (b.disabled = true));
  try {
    // A week is up to six uploads, each with a schedule, a push and a
    // read-back, plus a Garmin login. The 15 s default had the page report
    // "Not applied" while the server was still writing them.
    const r = await apiPost("/api/plan/apply", { proposal_id: id }, APPLY_TIMEOUT_MS);
    // A package that stopped half way is not a success story: the toast says
    // so, and the card keeps the button for the rest.
    toast(String(r?.result || "On Garmin."), { ms: 8000, warn: Number(r?.pending || 0) > 0 });
    context.onProposal?.(id, r?.proposal);
  } catch (e) {
    // A TIMEOUT is not a failure: the server may still be writing sessions to
    // Garmin. Re-enabling the button there invites a second apply against a
    // run that is still going - the one case the claim table then has to
    // catch. Leave it disabled and send the athlete to a refresh.
    if (/timed out|timeout/i.test(String(e.message || ""))) {
      toast("Still writing to Garmin - refresh in a moment to see what arrived.",
            { warn: true, ms: 10000 });
      return;
    }
    box?.querySelectorAll("button").forEach(b => (b.disabled = false));
    if (e.message === "403") return;
    toast("Not applied: " + (e.message || "server gone"), { warn: true, ms: 8000 });
    return;
  }
  context.onJobStart?.();              // pull fresh state: the card now says "On Garmin"
}

function renderFeedback(id, fb) {
  const value = fb?.value || "";
  const button = (v, sign, label) =>
    `<button type="button" class="fb-btn${value === v ? " active" : ""}" data-fb="${v}"
             aria-pressed="${value === v}" aria-label="${label}" title="${label}">${sign}</button>`;
  return `<div class="card-fb" data-fb-card="${esc(id)}">
    <span class="note">Helpful?</span>
    ${button("good", "👍", "good")}${button("bad", "👎", "bad")}
    <input type="text" class="fb-text" data-fb-text maxlength="500"
           placeholder="briefly why (optional)" aria-label="Feedback text"
           value="${esc(fb?.text || "")}">
    <button type="button" class="fb-send" data-fb-send>OK</button>
    ${fb?.ts ? `<span class="note fb-saved">saved ${esc(fmtAgo(fb.ts))}</span>` : ""}
    <button type="button" class="fb-del" data-card-del="${esc(id)}"
            title="Delete this card" aria-label="Delete this card">Delete</button>
  </div>`;
}

/** The field under a conversation. Each answer is written with the
 *  conversation as it stands when it is asked, so the server turns a question
 *  away while an answer is on its way (`answer_pending`) and the note says to
 *  wait - the field itself stays open: shut, it stayed shut for 45 minutes
 *  after a run that failed or was cancelled. */
export function renderFollowUp(id, pending, answers = 0) {
  return `<div class="card-followup card-thread-ask" data-fu-card="${esc(id)}">
    ${pending ? `<div class="note card-followup-pending">The answer arrives below${
       pending.job_id ? ` (${esc(pending.job_id)})` : ""} - ask on when it is here.</div>` : ""}
    <input type="text" class="fu-text" data-fu-text maxlength="1000"
           placeholder="${answers ? "Ask on…" : "Ask about this card…"}" aria-label="A question about this card">
    <button type="button" class="fu-send" data-fu-send>Ask</button>
  </div>`;
}

/** The app's cards a "next step" may start (`agent.NEXT_ACTIONS`), by their
 *  own titles: the button says what the app does, the coach only why. */
export const NEXT_TITLE = {
  "plan-week": "Plan my week", "train-today": "Should I train today?",
  "week-review": "Review my training week", "why-vo2max": "Why is VO2max moving?",
};

/** A check-in the card offers where the athlete said "it hurts" or "I have a
 *  cold": the Today tab's own button (app.js handles `data-check-in`), so a
 *  new conversation knows it too - the coach only points at it, their tap
 *  says it. The server lists it only on the day the card was written: a tap
 *  says it for today, and a card from last week is no word about now. */
//: The Today tab's own words for them (`logic.js: CHECK_IN_LABEL`): one name per button.
export const CHECK_IN_TITLE = { ill: "Ill", pain: "Pain", sore: "Sore legs", stressed: "Stressed" };
const CHECK_IN_EFFECT = { ill: "no run while it lasts", pain: "no run while it lasts",
                          sore: "nothing hard while it lasts", stressed: "nothing hard while it lasts" };

export function renderCheckIn(kind) {
  if (!Object.hasOwn(CHECK_IN_TITLE, kind)) return "";
  return `<div class="card-next"><span class="card-next-k">Tell the app</span><span class="card-next-one">
    <button type="button" class="btn btn-quiet" data-check-in="${esc(kind)}" data-check-in-do="say">${
      esc(CHECK_IN_TITLE[kind])}</button><span class="note">${esc(CHECK_IN_EFFECT[kind])} - the Today tab
      shows it and you end it there</span></span></div>`;
}

/** The next steps a card offers - each a button that starts that card with
 *  this conversation as its context (two taps: a run is not free). */
export function renderNext(id, next, root = id) {
  const steps = (Array.isArray(next) ? next : []).filter(n => n && NEXT_TITLE[n.action]).slice(0, 2);
  if (!steps.length) return "";
  return `<div class="card-next"><span class="card-next-k">Next step</span>${steps.map(n =>
    `<span class="card-next-one"><button type="button" class="btn btn-quiet" data-next="${esc(n.action)}"
       data-next-from="${esc(id)}" data-next-root="${esc(root)}"
       data-arm-label="Start? Tap again">→ ${esc(NEXT_TITLE[n.action])}</button>${
       n.why ? `<span class="note">${esc(n.why)}</span>` : ""}</span>`).join("")}</div>`;
}

/** What the card noted from the athlete's words - as the server keeps it now
 *  (a fact let go is no longer listed), and what it took the place of - with
 *  a way to let each go. The question it comes from is on the You tab. */
export function renderTold(told, full = false) {
  const items = (Array.isArray(told) ? told : []).filter(t => t && t.id && t.text);
  const fullNote = full === true ? `<div class="card-told-one"><span class="card-told-k">Not noted</span>
      your list for the coach is full - let one go on the You tab, then say it again.</div>` : "";
  if (!items.length) return fullNote ? `<div class="card-told">${fullNote}</div>` : "";
  return `<div class="card-told">${fullNote}${items.map(t =>
    `<div class="card-told-one"><span class="card-told-k">Noted for later</span> ${esc(t.text)}${
      t.instead ? ` <span class="note">(instead of: ${esc(t.instead)})</span>` : ""}
      <button type="button" class="linkish" data-forget="${esc(t.id)}"
        data-arm-label="Forget? Tap again">Forget</button></div>`).join("")}</div>`;
}

/* ── Card list ───────────────────────────────────────────────────────────── */

/** Renders `cards` into `container`. Remembers state on the element
 *  (re-render after tap/feedback/follow-up). `opts` as for renderCard, plus:
 *  autoOpen (default true: the newest NEW card expands), filter (pills by
 *  kind), emptyText (null = render nothing when empty), onJobStart. */
export function mountCards(container, cards, opts = {}) {
  install();
  const el = elOf(container);
  if (!el) return;
  el.dataset.cards = "1";
  cards = (cards || []).filter(c => c && cardId(c));
  state.set(el, { cards, opts });
  const key = keyOf(el);

  if (opts.autoOpen !== false && cards.length) {
    // Every card that shows up NEW and is the newest one expands — including
    // the one that was just paid for, not only on the first render.
    const before = seen.get(key) || new Set();
    const newest = cardId(cards[0]);
    if (!before.has(newest)) openCards.add(newest);
  }
  seen.set(key, new Set(cards.map(cardId)));

  const { main, children } = groupCards(cards);
  const kinds = [...new Set(main.map(c => c.kind).filter(Boolean))];
  let fk = filterKind.get(key) || "";
  if (fk && !kinds.includes(fk)) fk = "";
  const pills = opts.filter && kinds.length > 1
    ? `<div class="card-filter" role="group" aria-label="Filter by kind">${
        ["", ...kinds].map(k => `<button type="button" class="card-pill${fk === k ? " active" : ""}"
            data-card-filter="${esc(k)}" aria-pressed="${fk === k}">${
            esc(k ? (opts.kindTitle || {})[k] || k : "All")}</button>`).join("")}</div>`
    : "";
  const list = main.filter(c => !fk || c.kind === fk);

  const inputs = saveInputs(el);
  if (!list.length) {
    el.innerHTML = opts.emptyText === null ? "" : pills
      + `<div class="card"><div class="empty-note">${esc(opts.emptyText || "No analyses yet.")}</div></div>`;
    return;
  }
  el.innerHTML = pills + list.map(c =>
    renderCard(c, { ...opts, children: children.get(cardId(c)) || [] })).join("");
  restoreInputs(el, inputs);
}

/* Rescue typed text across an innerHTML rebuild: the job poll and a feedback
   tap must not empty a half-written "why". */
function saveInputs(el) {
  const m = new Map();
  el.querySelectorAll("[data-fb-text], [data-fu-text]").forEach(inp => {
    if (inp.value && inp.value !== inp.defaultValue) {
      const wrap = inp.closest("[data-fb-card], [data-fu-card]");
      const id = wrap?.dataset.fbCard || wrap?.dataset.fuCard;
      if (id) m.set((inp.hasAttribute("data-fb-text") ? "fb:" : "fu:") + id, inp.value);
    }
  });
  return m;
}
function restoreInputs(el, m) {
  if (!m.size) return;
  el.querySelectorAll("[data-fb-text], [data-fu-text]").forEach(inp => {
    const wrap = inp.closest("[data-fb-card], [data-fu-card]");
    const id = wrap?.dataset.fbCard || wrap?.dataset.fuCard;
    const v = m.get((inp.hasAttribute("data-fb-text") ? "fb:" : "fu:") + id);
    if (v != null) inp.value = v;
  });
}

function rerender(fromEl) {
  const cont = fromEl?.closest?.("[data-cards]");
  const z = cont && state.get(cont);
  if (z) { mountCards(cont, z.cards, z.opts); return cont; }
  context.rerender?.(fromEl);
  return null;
}

function findCard(fromEl, id) {
  const cont = fromEl?.closest?.("[data-cards]");
  const z = cont && state.get(cont);
  const source = z ? z.cards : (context.cards?.() || []);
  return source.find(c => cardId(c) === id) || null;
}

function optsOf(fromEl) {
  const cont = fromEl?.closest?.("[data-cards]");
  return (cont && state.get(cont)?.opts) || {};
}

/* ── Interaction (once, delegated) ───────────────────────────────────────── */

function install() {
  if (installed || typeof document === "undefined") return;
  installed = true;

  // The clocks of running jobs, every second - in place, no re-render, so an
  // open log or a half-typed question is never touched.
  setInterval(tickJobClocks, 1000);

  // `toggle` does not bubble — the capture phase on the document catches
  // every <details>.
  document.addEventListener("toggle", ev => {
    const d = ev.target;
    const k = d?.dataset?.det;
    if (!k) return;
    if (d.open) openDetails.add(k); else openDetails.delete(k);
    const evId = d.dataset.evidence;
    const act = evId ? evidenceToggle(d.open, evidenceCache.get(evId)) : null;
    if (act === "load") loadEvidence(evId, d.closest("[data-cards]") || d, d.contains(document.activeElement));
    else if (act === "forget") evidenceCache.delete(evId);
  }, true);

  document.addEventListener("click", ev => {
    const head = ev.target.closest("button[data-card]");
    if (head) {
      const id = head.dataset.card;
      if (openCards.has(id)) openCards.delete(id); else openCards.add(id);
      rerender(head);
      // The re-render replaces the button — find the focus again, otherwise
      // it lands on <body> and the next Tab starts from the very top.
      document.querySelector(`button[data-card="${CSS.escape(id)}"]`)?.focus();
      return;
    }
    const pill = ev.target.closest("[data-card-filter]");
    if (pill) {
      const cont = pill.closest("[data-cards]");
      if (cont) { filterKind.set(keyOf(cont), pill.dataset.cardFilter || ""); rerender(pill); }
      return;
    }
    const fb = ev.target.closest("[data-fb]");
    if (fb) {
      const wrap = fb.closest("[data-fb-card]");
      const current = findCard(wrap, wrap.dataset.fbCard)?.feedback?.value || "";
      sendFeedback(wrap, current === fb.dataset.fb ? "" : fb.dataset.fb);
      return;
    }
    const fbs = ev.target.closest("[data-fb-send]");
    if (fbs) {
      const wrap = fbs.closest("[data-fb-card]");
      sendFeedback(wrap, findCard(wrap, wrap.dataset.fbCard)?.feedback?.value || "");
      return;
    }
    const fus = ev.target.closest("[data-fu-send]");
    if (fus) { sendFollowUp(fus.closest("[data-fu-card]")); return; }
    const nx = ev.target.closest("[data-next]");
    if (nx) {
      armOrFire(nx, "next:" + nx.dataset.nextFrom + nx.dataset.next,
                () => startNext(nx.dataset.nextFrom, nx.dataset.next, nx, nx.dataset.nextRoot));
      return;
    }
    const fg = ev.target.closest("[data-forget]");
    if (fg) {
      armOrFire(fg, "forget:" + fg.dataset.forget, () => forgetTold(fg.dataset.forget, fg));
      return;
    }
    const apply = ev.target.closest("[data-apply]");
    if (apply) {
      // Two taps: this is the write to the athlete's Garmin account.
      armOrFire(apply, "apply:" + apply.dataset.apply,
                () => applyProposal(apply.dataset.apply, apply));
      return;
    }
    const undo = ev.target.closest("[data-undo]");
    if (undo) {
      // Two taps as well: it changes the athlete's Garmin calendar, and a
      // session taken off by accident is a session they turn up without.
      armOrFire(undo, "undo:" + undo.dataset.undo,
                () => undoProposal(undo.dataset.undo, undo));
      return;
    }
    const del = ev.target.closest("[data-card-del]");
    if (del) {
      // Two taps: the card is gone for good, and deleting the newest card of a
      // kind also drops the memory the next run of that kind builds on.
      armOrFire(del, "card-del:" + del.dataset.cardDel,
                () => deleteCard(del.dataset.cardDel, del));
      return;
    }
    const log = ev.target.closest("[data-job-log]");
    if (log) { toggleLog(log.dataset.jobLog, log); return; }
    const cancel = ev.target.closest("[data-job-cancel]");
    if (cancel) {
      const id = cancel.dataset.jobCancel;
      armOrFire(cancel, "job-cancel:" + id, () => cancelJob(id, cancel));
    }
  });

  document.addEventListener("keydown", ev => {
    if (ev.key !== "Enter") return;
    if (ev.target.matches?.("[data-fu-text]")) {
      ev.preventDefault(); sendFollowUp(ev.target.closest("[data-fu-card]"));
    } else if (ev.target.matches?.("[data-fb-text]")) {
      ev.preventDefault();
      const wrap = ev.target.closest("[data-fb-card]");
      sendFeedback(wrap, findCard(wrap, wrap.dataset.fbCard)?.feedback?.value || "");
    }
  });
}

/* The only exit from a poisoned card.

   `cleanup_cards()` pins the NEWEST card of each kind forever ("it is the dedup
   anchor and the memory of the next run") and `previous_card_block()` feeds it
   back into every later card of that kind. So a card steered by an injected
   workout label keeps steering until it is deleted. The server route and the
   README both existed; the button did not, and the only remedy was to find
   `~/.runspector/cards/` in a file manager. */
async function deleteCard(id, btn) {
  if (!id) return;
  try {
    await apiPost(`/api/cards/${encodeURIComponent(id)}/delete`, {});
  } catch (e) {
    toast("Could not delete the card: " + (e.message || e), { warn: true });
    return;
  }
  const card = btn?.closest("[data-card-id]");
  if (card) card.remove();
  toast("Card deleted.");
  context.onJobStart?.();              // pull fresh state: the list has changed
}

/* Optimistic: the card carries the feedback at once, the server confirms. If
   it fails, the old state comes back and the error is visible. */
async function sendFeedback(wrap, value) {
  if (!wrap) return;
  const id = wrap.dataset.fbCard;
  const card = findCard(wrap, id);
  if (!card) return;
  const text = (wrap.querySelector("[data-fb-text]")?.value || "").trim().slice(0, 500);
  const before = card.feedback;
  card.feedback = (value || text) ? { value, text, ts: new Date().toISOString() } : null;
  rerender(wrap);
  try {
    const r = await apiPost("/api/feedback", { card_id: id, value, text });
    // The server may echo the stored object; if it does not, the optimistic
    // one stands.
    if (r && "feedback" in r) card.feedback = r.feedback || null;
  } catch (e) {
    card.feedback = before;
    if (e.message !== "403") toast("Feedback not saved: " + (e.message || "server gone"), { warn: true });
  }
  const fresh = document.querySelector(`[data-fb-card="${CSS.escape(id)}"]`);
  rerender(fresh || wrap);
}

async function startNext(fromCard, action, btn, root = fromCard) {
  btn.disabled = true;
  try {
    const r = await apiPost("/api/spawn", { template_id: action, from_card: fromCard });
    // Pending on the conversation's card: that is where its field says so.
    followUps.set(root, { job_id: String(r?.job?.id || ""), ts: Date.now() });
    toast(`${NEXT_TITLE[action] || "The next step"} is running - it arrives in this conversation.`,
          { ms: 6000 });
    const cont = rerender(btn);
    (optsOf(cont || btn).onJobStart || context.onJobStart)?.(r);
  } catch (e) {
    btn.disabled = false;
    if (e.message === "403") return;
    if (e.status === 409 && handle409(e)) return;
    toast("Not started: " + (e.data?.error || e.message || "server gone"), { warn: true, ms: 6000 });
  }
}

/** Let a kept fact go - on a card or on the You tab; the page then pulls the
 *  state again, so both places say so. */
export async function forgetTold(entryId, btn) {
  if (btn) btn.disabled = true;
  try {
    await apiPost("/api/profile/forget", { id: entryId });
    toast("Let go - new conversations no longer get it. One that already used it keeps it until you delete that card.");
    await context.onChanged?.();
  } catch (e) {
    if (btn) btn.disabled = false;
    if (e.message === "403") return;
    toast("Could not let it go: " + (e.data?.error || e.message || "server gone"), { warn: true });
  }
}

async function sendFollowUp(wrap) {
  if (!wrap || wrap.dataset.sending) return;
  const id = wrap.dataset.fuCard;
  const inp = wrap.querySelector("[data-fu-text]");
  const prompt = (inp?.value || "").trim();
  if (!prompt) { inp?.focus(); return; }
  wrap.dataset.sending = "1";
  wrap.querySelectorAll("button, input").forEach(b => (b.disabled = true));
  try {
    // `from_job` wants the JOB behind the card; a card without its own
    // `job_id` was written under the job's id.
    const fromJob = String(findCard(wrap, id)?.job_id || id);
    const r = await apiPost("/api/spawn", { from_job: fromJob, prompt });
    followUps.set(id, { job_id: String(r?.job?.id || ""), ts: Date.now() });
    toast("Your question is with the coach - the answer arrives below, in this conversation.", { ms: 6000 });
    const cont = rerender(wrap);
    (optsOf(cont || wrap).onJobStart || context.onJobStart)?.(r);
  } catch (e) {
    delete wrap.dataset.sending;
    wrap.querySelectorAll("button, input").forEach(b => (b.disabled = false));
    if (e.message === "403") return;
    if (e.status === 409 && handle409(e)) return;
    toast("Follow-up not started: " + (e.message || "server gone"), { warn: true, ms: 6000 });
  }
}

/* ── Job strip ───────────────────────────────────────────────────────────── */

/** Active jobs (with their interim `note`, e.g. "waiting for quota", an
 *  expectation from the average duration, cancel with arm-and-confirm) and the
 *  failures of the last 24 h with their reason. `opts`: finished (also show
 *  completed runs), cancel (false = no cancel button), onChange (after a
 *  cancel), title. */
export function mountJobs(container, jobs, opts = {}) {
  install();
  const el = elOf(container);
  if (!el) return;
  el.dataset.jobs = "1";
  jobsState.set(el, { jobs, opts });
  resetArm(el, { keep: true });       // its own Cancel is armed again after the rebuild
  const list = jobsForStrip(jobs, { finished: !!opts.finished });
  if (!list.length) { el.innerHTML = ""; rearmIn(el); return; }
  const active = list.filter(isActive).length;
  const avg = avgDurationSec(jobs);
  jobsDrawnAt = Date.now();
  el.innerHTML = `<div class="card job-strip">
    <div class="subhead">${esc(opts.title || "Jobs")}${active ? ` · ${active} active` : ""}</div>
    ${list.map(j => renderJobRow(j, avg, opts)).join("")}
  </div>`;
  rearmIn(el);
  // An open log of a job that is still running goes stale with every poll —
  // pull it again, quietly, and patch the <pre> in place.
  for (const j of list) if (isActive(j) && jobLogs.has(j.id)) loadLog(j.id).catch(() => {});
}

function renderJobRow(j, avg, opts) {
  const active = isActive(j);
  const when = active
    ? (j.started ? runningFor(j.started) : "waiting")
    : fmtAgo(j.finished || j.started || j.created);
  const expectation = active && avg ? ` · expect ${fmtExpectation(avg)}` : "";
  // A running job's clock ticks between polls: `tickJobClocks` rewrites it.
  const clock = active && j.started
    ? ` data-job-since="${esc(j.started)}" data-job-expect="${esc(expectation)}"` : "";
  // Active jobs carry their interim message in `note` ("waiting for quota"),
  // failed ones their reason in `result_summary`. For finished jobs that field
  // holds the result — the card shows that, not the strip.
  // A failure in words a person can act on (`plainJobError`); the technical
  // reason is in the job's log, one tap away.
  const reasonText = active ? (j.note || j.result_summary)
                   : j.status !== "done" ? plainJobError(j.status, j.result_summary || j.note) : "";
  const reason = reasonText
    ? `<div class="job-reason${active ? "" : " job-reason-error"}">${esc(truncate(reasonText, 240))}</div>`
    : "";
  const lines = jobLogs.get(j.id);
  return `<div class="job-line${active ? " job-active" : ""}" data-job-id="${esc(j.id)}">
    <div class="job-row-head">
      <span class="pill ${JOB_PILL[j.status] || "pill-unknown"}">${
        j.status === "running" ? '<span class="live-dot"></span>' : ""}${esc(JOB_LABEL[j.status] || j.status)}</span>
      <span class="job-title">${esc(j.title || j.id)}</span>
      <span class="note job-when"${clock}>${esc(when)}${esc(expectation)}</span>
      <button type="button" class="job-log-btn" data-job-log="${esc(j.id)}"
              aria-expanded="${!!lines}">Log</button>
      ${active && opts.cancel !== false
        ? `<button type="button" class="job-cancel" data-job-cancel="${esc(j.id)}"
                   data-arm-key="job-cancel:${esc(j.id)}"
                   data-arm-label="Really cancel?" aria-label="Cancel run">✕ Cancel</button>` : ""}
    </div>
    ${reason}
    ${lines && !active && j.status !== "done" && (j.result_summary || j.note)
      ? `<div class="note mono job-tech">The technical reason: ${esc(truncate(j.result_summary || j.note, 300))}</div>` : ""}
    ${lines ? `<pre class="job-log mono" data-job-log-out="${esc(j.id)}">${esc(logText(lines))}</pre>` : ""}
  </div>`;
}

const logText = lines => (lines.length ? lines.slice(-200).join("\n") : "(log is empty)");

/** Fetch a card's record, then draw the card again with it. `where` is taken
 *  before the wait: the job poll may have rebuilt the card meanwhile. Drawing
 *  again replaces the disclosure, so keyboard focus is put back on it. */
async function loadEvidence(id, where, focused = false) {
  const draw = () => {
    const cont = rerender(where);
    if (focused) {
      (cont || document).querySelector(`details[data-evidence="${CSS.escape(id)}"] > summary`)?.focus();
    }
  };
  evidenceCache.set(id, "loading");
  draw();
  try {
    const r = await apiGet(`/api/cards/${encodeURIComponent(id)}/evidence`);
    evidenceCache.set(id, r?.evidence || { error: "empty answer" });
  } catch (e) {
    evidenceCache.set(id, { error: e.message || "server gone" });
  }
  draw();
}

/** A running job's clock text, or null when the strip was last drawn too long
 *  ago to trust: the poll stops on a hidden tab, and a job "running for 1 h"
 *  may long be done - then the clock stands still, as it did before it ticked. */
export function jobClock(since, expect, drawnAt, now = Date.now()) {
  if (now - drawnAt > 30_000) return null;
  return runningFor(since, now) + (expect || "");
}

let jobsDrawnAt = 0;

function tickJobClocks() {
  if (document.hidden) return;
  document.querySelectorAll("[data-job-since]").forEach(el => {
    const t = jobClock(el.dataset.jobSince, el.dataset.jobExpect, jobsDrawnAt);
    if (t !== null) el.textContent = t;
  });
}

async function loadLog(id) {
  const r = await apiGet(`/api/jobs/${encodeURIComponent(id)}/log`);
  const lines = (Array.isArray(r?.lines) ? r.lines : []).map(String);
  if (!jobLogs.has(id)) return;                    // closed in the meantime
  jobLogs.set(id, lines);
  const out = document.querySelector(`[data-job-log-out="${CSS.escape(id)}"]`);
  if (out) out.textContent = logText(lines);       // textContent: log lines are foreign text
}

async function toggleLog(id, btn) {
  const cont = btn.closest("[data-jobs]");
  const z = cont && jobsState.get(cont);
  const redraw = () => { if (z) mountJobs(cont, z.jobs, z.opts); };
  if (jobLogs.has(id)) { jobLogs.delete(id); redraw(); return; }
  jobLogs.set(id, ["loading…"]);
  redraw();
  try { await loadLog(id); }
  catch (e) {
    jobLogs.delete(id); redraw();
    if (e.message !== "403") toast("Log not readable: " + (e.message || "server gone"), { warn: true });
  }
}

async function cancelJob(id, btn) {
  const cont = btn.closest("[data-jobs]");
  const opts = (cont && jobsState.get(cont)?.opts) || {};
  btn.disabled = true;
  try {
    await apiPost(`/api/jobs/${encodeURIComponent(id)}/cancel`, {});
    toast("Cancel requested — the run is being stopped.");
    await opts.onChange?.();
  } catch (e) {
    btn.disabled = false;
    if (e.message !== "403") toast("Cancel refused: " + (e.message || "server gone"), { warn: true });
  }
}

/* ── Poller ──────────────────────────────────────────────────────────────── */

/** ONE poller. Asks ONLY the slim /api/jobs (not the whole state) while a run
 *  is active, stops itself in the background and when idle; on the last
 *  transition active→finished it runs `onFinished` (the page then loads the
 *  full state with the new card). `jobs()` returns the current list,
 *  `setJobs(list)` takes the fresh one and renders what depends on it. A
 *  network drop-out does not matter (phone: Wi-Fi → mobile data), only a 403
 *  ends the poll. */
export function jobPoller({ jobs, setJobs, onFinished, intervalMs = 8000 }) {
  let timer = null;
  const stop = () => { if (timer) clearInterval(timer); timer = null; };
  async function tick() {
    if (document.hidden || !(jobs() || []).some(isActive)) { stop(); return; }
    let all;
    try { all = await apiGet("/api/jobs"); }
    catch (e) { if (e.status === 403) stop(); return; }
    const list = Array.isArray(all) ? all : all?.jobs || [];
    setJobs(list);
    if (!list.some(isActive)) { stop(); await onFinished?.(); }
  }
  return {
    start() {
      if (timer || !(jobs() || []).some(isActive)) return;
      timer = setInterval(() => tick().catch(() => {}), intervalMs);
    },
    stop,
    running: () => timer !== null,
  };
}

/* ── Spawn with the dedup answer ─────────────────────────────────────────── */

/** Translate the server's 409 into an action. Returns true if it was dealt
 *  with here. `recompute` repeats the request with force:true — the toast
 *  action IS the confirmation, no second arming. */
export function handle409(e, recompute) {
  const k = classify409(e);
  if (k.type === "running") {
    toast(`Already running${k.ref ? ` (${k.ref})` : ""} — the card arrives as soon as the run is done.`,
          { warn: true, ms: 6000 });
    return true;
  }
  if (k.type === "current") {
    toast(String(e?.message || "Already computed on the current data."),
          recompute ? { ms: 12000, action: "Recompute", onAction: recompute } : { ms: 8000 });
    return true;
  }
  if (k.type === "queue_full") {
    toast(String(e?.message || "The queue is full — wait for a run to finish."),
          { warn: true, ms: 6000 });
    return true;
  }
  return false;
}

/* ── The planned week, seen and changed by the athlete (roadmap 19) ────────── */

/*    Live use: a week was planned - Mon, Tue, Sat - and the athlete saw it did
   not fit. The strip showed three boxes with a name each, nothing about what
   was behind them, and no way to say "Friday, not Saturday". A tap on a day
   opens it: what the session is, and the changes the athlete can make. A
   change is only a proposal - the server files it with the coach's word on it
   (`plan.propose_change`), and the calendar changes with the same "Put on
   watch" every proposal has.

   Pure string functions; `app.js` wires them, `web-tests/week.test.mjs` tests
   them. Everything from the server is escaped here. They live in this module,
   not one of their own: one module more made headless Chrome's page load miss
   its time budget in about one run in ten (the smoke test), and this one is
   loaded anyway. */

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
