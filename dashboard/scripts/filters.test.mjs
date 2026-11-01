import test from "node:test";
import assert from "node:assert/strict";
import {
  expirationDates,
  filterInstruments,
  strategyLabel,
} from "../src/filters.ts";
const instrument = (symbol, type, subtype, expiries, nonempty = 1) => ({
  symbol,
  type,
  subtype,
  nonempty,
  definition: {
    Leg_Data: expiries.map((EXPIRE_DATE) => ({
      EXPIRE_DATE,
      OSI_ROOT: "SPX",
      STRIKE: "6000.0000",
    })),
  },
});
const items = [
  instrument("box", "QSB", "BOX_SPREAD", ["2026-11-06", "2026-11-06"]),
  instrument("swap", "QSB", "BOX_SWAP", ["2026-11-06", "2026-12-18"], 3),
  instrument("roll", "QSB", "JELLY_ROLL", ["2026-12-18", "2027-01-15"], 2),
  instrument("condor", "CPM", "IRON_CONDOR", ["2026-11-06"]),
  instrument("unknown", "QSB", "UNKNOWN", []),
];
test("strategy filters preserve distinct box spreads, swaps and jelly rolls", () => {
  for (const [strategy, symbol] of [
    ["BOX_SPREAD", "box"],
    ["BOX_SWAP", "swap"],
    ["JELLY_ROLL", "roll"],
  ])
    assert.deepEqual(
      filterInstruments(items, "ALL", strategy, "ALL", "").map((i) => i.symbol),
      [symbol],
    );
  assert.equal(strategyLabel("JELLY_ROLL"), "Jelly Roll");
});
test("expiration matches any leg without duplicates and combines with other filters", () => {
  assert.deepEqual(expirationDates(items[0]), ["2026-11-06"]);
  assert.deepEqual(
    filterInstruments(items, "QSB", "ALL", "2026-12-18", "").map(
      (i) => i.symbol,
    ),
    ["swap", "roll"],
  );
  assert.deepEqual(
    filterInstruments(
      items,
      "QSB",
      "JELLY_ROLL",
      "2026-12-18",
      "6000.0000",
    ).map((i) => i.symbol),
    ["roll"],
  );
  assert.deepEqual(
    filterInstruments(items, "CPM", "JELLY_ROLL", "ALL", ""),
    [],
  );
  assert.deepEqual(
    filterInstruments(items, "ALL", "ALL", "2027-01-15", " BOX "),
    [],
  );
  assert.equal(filterInstruments(items, "ALL", "ALL", "ALL", "").length, 5);
});
