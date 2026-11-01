import type { Instrument } from "./data";
import { legLabel } from "./exchange.ts";

export function strategyLabel(value: string): string {
  return value
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
export function expirationDates(instrument: Instrument): string[] {
  return [
    ...new Set(
      (instrument.definition.Leg_Data || [])
        .map((l) => l.EXPIRE_DATE)
        .filter((d): d is string => !!d),
    ),
  ].sort();
}
export function filterInstruments(
  instruments: Instrument[],
  book: string,
  strategy: string,
  expiry: string,
  query: string,
): Instrument[] {
  const q = query.trim().toLowerCase();
  return instruments
    .filter(
      (i) =>
        (book === "ALL" || i.type === book) &&
        (strategy === "ALL" || i.subtype === strategy) &&
        (expiry === "ALL" || expirationDates(i).includes(expiry)) &&
        (!q ||
          [i.symbol, i.subtype, ...(i.definition.Leg_Data || []).map(legLabel)]
            .join(" ")
            .toLowerCase()
            .includes(q)),
    )
    .sort(
      (a, b) => b.nonempty - a.nonempty || a.symbol.localeCompare(b.symbol),
    );
}
