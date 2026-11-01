import { exchangeDate } from "./exchange.ts";
/** Keep elapsed hours, including both repeated hours when daylight saving ends. */
export function hourlySlots<T extends { at: string }>(
  hours: T[],
): (T | null)[] {
  if (!hours.length) return [];
  const entries = hours.map((h) => [Date.parse(h.at), h] as const);
  if (entries.some(([at]) => !Number.isFinite(at)))
    throw new Error("Invalid summary hour");
  const first = Math.min(...entries.map(([at]) => at));
  const last = Math.max(...entries.map(([at]) => at));
  const count = (last - first) / 3600000 + 1;
  if (
    !Number.isInteger(count) ||
    count > 25 ||
    exchangeDate(first) !== exchangeDate(last)
  )
    throw new Error("Summary must cover at most one Eastern calendar day");
  const byTime = new Map(entries);
  return Array.from(
    { length: count },
    (_, i) => byTime.get(first + i * 3600000) || null,
  );
}
