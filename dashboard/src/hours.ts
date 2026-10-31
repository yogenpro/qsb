/** Keep missing UTC hours as empty slots instead of compressing time. */
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
  if (!Number.isInteger(count) || count > 24)
    throw new Error("Summary must cover at most one UTC day");
  const byTime = new Map(entries);
  return Array.from(
    { length: count },
    (_, i) => byTime.get(first + i * 3600000) || null,
  );
}
