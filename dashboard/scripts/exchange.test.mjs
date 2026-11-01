import test from "node:test";
import assert from "node:assert/strict";
import {
  collectionDates,
  detailPathsForDate,
  easternIndex,
  exchangeClock,
  exchangeDate,
  isTradingDate,
  earlyClose,
  legLabel,
  regularMinute,
  sourceDays,
} from "../src/exchange.ts";
import { hourlySlots } from "../src/hours.ts";
import { time } from "../src/data.ts";

test("Eastern date and clock observe daylight saving and local midnight", () => {
  assert.equal(exchangeClock("2026-01-20T14:30:00Z"), "09:30");
  assert.equal(exchangeClock("2026-07-20T13:30:00Z"), "09:30");
  assert.equal(time("2026-01-20T14:30:00Z"), "09:30:00 EST");
  assert.equal(time("2026-07-20T13:30:00Z"), "09:30:00 EDT");
  assert.equal(exchangeDate("2026-10-31T03:59:00Z"), "2026-10-30");
  assert.equal(exchangeDate("2026-10-31T04:00:00Z"), "2026-10-31");
  const hours = Array.from({ length: 25 }, (_, i) => ({
    at: new Date(
      Date.parse("2026-11-01T04:00:00Z") + i * 3600000,
    ).toISOString(),
  }));
  assert.equal(hourlySlots(hours).length, 25);
  assert.equal(exchangeClock(hours[1].at), "01:00");
  assert.equal(exchangeClock(hours[2].at), "01:00");
});
test("closed RTH days are hidden and early closes remain selectable", () => {
  for (const date of [
    "2026-10-31",
    "2026-11-01",
    "2026-01-01",
    "2026-01-19",
    "2026-02-16",
    "2026-04-03",
    "2026-05-25",
    "2026-06-19",
    "2026-07-03",
    "2026-09-07",
    "2026-11-26",
    "2026-12-25",
  ])
    assert.equal(isTradingDate(date), false, date);
  assert.equal(isTradingDate("2026-10-30"), true);
  assert.equal(earlyClose("2026-11-27"), true);
  assert.equal(earlyClose("2026-12-24"), true);
  assert.equal(isTradingDate("2021-12-31"), true);
  const manifest = {
    days: [
      { date: "2026-10-31", attempts: 10 },
      { date: "2026-11-01", attempts: 10 },
    ],
  };
  assert.deepEqual(collectionDates(manifest), ["2026-10-30"]);
});
test("session shading splits 09:30, index close, expiry close and early close", () => {
  const legs = [{ OSI_ROOT: "SPXW", EXPIRE_DATE: "2026-12-18" }];
  const regular = (at) => regularMinute(Date.parse(at), legs);
  assert.equal(regular("2026-10-30T13:15:00Z"), false);
  assert.equal(regular("2026-10-30T13:30:00Z"), true);
  assert.equal(regular("2026-10-30T20:00:00Z"), true);
  assert.equal(regular("2026-10-30T20:15:00Z"), false);
  assert.equal(regular("2026-11-27T17:45:00Z"), true);
  assert.equal(regular("2026-11-27T18:00:00Z"), false);
  assert.equal(
    regularMinute(Date.parse("2026-10-30T20:00:00Z"), [
      { OSI_ROOT: "SPXW", EXPIRE_DATE: "2026-10-30" },
    ]),
    false,
  );
});
const hour = (at, attempts, valid = attempts) => ({
  at,
  attempts,
  valid,
  nonempty: valid,
  bid_min: "1.0000",
  bid_max: "2.0000",
  ask_min: null,
  ask_max: null,
});
test("Eastern day joins two partitions without leaking neighbouring-day counts", () => {
  const item = {
    id: "a",
    details: ["early"],
    hourly: [hour("2026-10-30T03:00:00Z", 50), hour("2026-10-30T04:00:00Z", 2)],
  };
  const later = {
    ...item,
    details: ["late"],
    hourly: [
      hour("2026-10-31T03:00:00Z", 3, 2),
      hour("2026-10-31T04:00:00Z", 100),
    ],
  };
  const index = easternIndex(
    [{ instruments: [item] }, { instruments: [later] }],
    "2026-10-30",
  );
  assert.equal(index.instruments[0].attempts, 5);
  assert.equal(index.instruments[0].valid, 4);
  assert.equal(index.instruments[0].failed, 1);
  assert.deepEqual(index.instruments[0].details, ["early", "late"]);
  assert.equal(index.instruments[0].hourly[0].bid_min, "1.0000");
  assert.deepEqual(
    sourceDays(
      {
        days: [
          { date: "2026-10-29" },
          { date: "2026-10-31" },
          { date: "2026-10-30" },
        ],
      },
      "2026-10-30",
    ).map((d) => d.date),
    ["2026-10-30", "2026-10-31"],
  );
});
test("replay partitions crossing Eastern midnight retain only overlapping parts", async () => {
  const parts = [
    ["2026-10-30T02:00:00Z", "2026-10-30T03:00:00Z"],
    ["2026-10-30T03:59:00Z", "2026-10-30T04:01:00Z"],
    ["2026-10-31T03:59:00Z", "2026-10-31T04:01:00Z"],
    ["2026-10-31T05:00:00Z", "2026-10-31T06:00:00Z"],
  ];
  const paths = parts.map((_, i) => String(i));
  const read = async (p) => ({
    observations: parts[Number(p)].map((at) => ({ at })),
  });
  assert.deepEqual(await detailPathsForDate(paths, "2026-10-30", read), [
    "1",
    "2",
  ]);
  assert.deepEqual(await detailPathsForDate(paths, "2026-11-02", read), []);
});
test("leg labels keep exact strike precision and explicit missing fields", () => {
  assert.equal(
    legLabel({
      BUY_SELL_FLAG: "B",
      Leg_Ratio: "1",
      OSI_ROOT: "SPXW",
      STRIKE: "6000.0000",
      EXPIRE_DATE: "2026-12-18",
    }),
    "B 1 · SPXW · 6000.0000 · 2026-12-18",
  );
  assert.match(legLabel({}), /\?/);
});
