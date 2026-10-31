import test from "node:test";
import assert from "node:assert/strict";
import { hourlySlots } from "../src/hours.ts";
test("missing observation hours retain their space on the time axis", () => {
  const a = { at: "2026-10-30T13:00:00+00:00" },
    b = { at: "2026-10-30T16:00:00+00:00" };
  assert.deepEqual(hourlySlots([a, b]), [a, null, null, b]);
  assert.deepEqual(hourlySlots([b, a]), [a, null, null, b]);
});
test("single and empty sessions stay bounded", () => {
  assert.deepEqual(hourlySlots([]), []);
  const a = { at: "2026-10-30T13:00:00+00:00" };
  assert.deepEqual(hourlySlots([a]), [a]);
});
test("malformed or multi-day summary axes are rejected", () => {
  assert.throws(() => hourlySlots([{ at: "bad" }]));
  assert.throws(() =>
    hourlySlots([
      { at: "2026-10-29T00:00:00Z" },
      { at: "2026-10-30T00:00:00Z" },
    ]),
  );
});
