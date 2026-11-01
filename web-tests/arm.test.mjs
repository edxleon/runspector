/* The two-tap confirmation (ui.js `armOrFire`). The bug it pins: the job
 * strip redraws every 8 s while a coach run is active and called `resetArm()`
 * for the whole page, so "Send? Tap again" on the Ask button vanished before
 * a person could tap it - a first tap that seemed to do nothing. A redraw now
 * disarms only buttons inside what it redraws. No jsdom: a stub big enough
 * for the arming. */
import test from "node:test";
import assert from "node:assert/strict";

globalThis.document = { querySelectorAll: () => [], documentElement: { dataset: {} } };
const { armOrFire, resetArm, ARM_MS } = await import("../src/runspector/web/static/ui.js");

function button(label) {
  const classes = new Set();
  return {
    innerHTML: label, dataset: { armLabel: "Send? Tap again" }, isConnected: true,
    set textContent(v) { this.innerHTML = v; },
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), has: c => classes.has(c) },
  };
}

test("a redraw of the job strip does not disarm the Ask button", () => {
  const ask = button("Ask");
  let fired = 0;
  armOrFire(ask, "ask:q", () => { fired += 1; });
  assert.equal(ask.innerHTML, "Send? Tap again");
  const strip = { contains: b => b !== ask };              // the strip holds other buttons
  resetArm(strip);
  assert.equal(ask.innerHTML, "Send? Tap again", "still armed after the strip's redraw");
  armOrFire(ask, "ask:q", () => { fired += 1; });
  assert.equal(fired, 1);
  assert.equal(ask.innerHTML, "Ask");
});

test("a redraw of what holds the button still disarms it", () => {
  const cancel = button("Cancel");
  armOrFire(cancel, "job-cancel:1", () => {});
  resetArm({ contains: b => b === cancel });
  assert.equal(cancel.innerHTML, "Cancel");
  resetArm();                                             // and a full redraw, as before
});

test("the second tap has long enough to be read", () => {
  assert.ok(ARM_MS >= 8000, ARM_MS);
});

test("Really cancel? survives the strip's own redraw", async () => {
  const { rearmIn } = await import("../src/runspector/web/static/ui.js");
  const old = button("✕ Cancel");
  old.dataset.armKey = "job-cancel:7";
  armOrFire(old, "job-cancel:7", () => {});
  const strip = { contains: b => b === old, querySelector: () => fresh };
  resetArm(strip, { keep: true });                       // the strip is about to redraw
  old.isConnected = false;                               // ...and has replaced the button
  const fresh = button("✕ Cancel");
  fresh.dataset.armKey = "job-cancel:7";
  rearmIn(strip);
  assert.equal(fresh.innerHTML, "Send? Tap again", "the new button is armed");
  let fired = 0;
  armOrFire(fresh, "job-cancel:7", () => { fired += 1; });
  assert.equal(fired, 1);
});
