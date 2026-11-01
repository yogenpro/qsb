import type { Detail, Index, Instrument, Leg, Manifest } from "./data";

export const exchangeZone = "America/New_York";
const dateFormat = new Intl.DateTimeFormat("en-CA", {
  timeZone: exchangeZone,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});
const clockFormat = new Intl.DateTimeFormat("en-GB", {
  timeZone: exchangeZone,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
export function exchangeDate(at: string | number): string {
  const parts = dateFormat.formatToParts(new Date(at));
  return ["year", "month", "day"]
    .map((k) => parts.find((p) => p.type === k)!.value)
    .join("-");
}
export function exchangeClock(at: string | number): string {
  return clockFormat.format(new Date(at));
}
export function shiftDate(date: string, days: number): string {
  return new Date(Date.parse(date + "T12:00:00Z") + days * 86400000)
    .toISOString()
    .slice(0, 10);
}
function weekday(date: string): number {
  return new Date(date + "T12:00:00Z").getUTCDay();
}
function nthWeekday(
  year: number,
  month: number,
  day: number,
  nth: number,
): string {
  const first = `${year}-${String(month).padStart(2, "0")}-01`;
  return shiftDate(first, ((day - weekday(first) + 7) % 7) + 7 * (nth - 1));
}
function observed(date: string): string {
  return shiftDate(
    date,
    weekday(date) === 6 ? -1 : weekday(date) === 0 ? 1 : 0,
  );
}
function goodFriday(year: number): string {
  // Gregorian Easter, then the preceding Friday.
  const a = year % 19,
    b = Math.floor(year / 100),
    c = year % 100;
  const d = Math.floor(b / 4),
    e = b % 4,
    f = Math.floor((b + 8) / 25);
  const g = Math.floor((b - f + 1) / 3),
    h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4),
    k = c % 4,
    l = (32 + 2 * e + 2 * i - h - k) % 7;
  const m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31);
  const day = ((h + l - 7 * m + 114) % 31) + 1;
  return shiftDate(
    `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`,
    -2,
  );
}
// Cboe US options RTH calendar; reviewed against the published 2026 schedule.
// Exceptional closures belong here; review annually when Cboe publishes its calendar.
const exceptionalClosures = new Set(["2025-01-09"]);
export function isTradingDate(date: string): boolean {
  if ([0, 6].includes(weekday(date)) || exceptionalClosures.has(date))
    return false;
  const y = Number(date.slice(0, 4));
  const mayLast = `${y}-05-31`;
  const holidays = [
    // A Saturday New Year's Day does not close the preceding Friday.
    weekday(`${y}-01-01`) === 0 ? `${y}-01-02` : `${y}-01-01`,
    nthWeekday(y, 1, 1, 3),
    nthWeekday(y, 2, 1, 3),
    goodFriday(y),
    shiftDate(mayLast, -(weekday(mayLast) + 6) % 7),
    observed(`${y}-07-04`),
    nthWeekday(y, 9, 1, 1),
    nthWeekday(y, 11, 4, 4),
    observed(`${y}-12-25`),
  ];
  if (y >= 2022) holidays.push(observed(`${y}-06-19`));
  return !holidays.includes(date);
}
export function earlyClose(date: string): boolean {
  const y = Number(date.slice(0, 4));
  return (
    isTradingDate(date) &&
    (date === shiftDate(nthWeekday(y, 11, 4, 4), 1) ||
      date === `${y}-12-24` ||
      date === `${y}-07-03`)
  );
}
export function regularClose(date: string, legs: Leg[]): number {
  if (earlyClose(date)) return 13 * 60;
  const lateRoots = /^(SPXW?|XSP|VIXW?|RUTW?|MRUT|SPY|QQQ|IWM|DIA|OEX|XEO)$/;
  return legs.length && legs.every((l) => lateRoots.test(l.OSI_ROOT || ""))
    ? legs.some((l) => l.OSI_ROOT === "SPXW" && l.EXPIRE_DATE === date)
      ? 16 * 60
      : 16 * 60 + 15
    : 16 * 60;
}
export function regularMinute(at: number, legs: Leg[]): boolean {
  const date = exchangeDate(at),
    [h, m] = exchangeClock(at).split(":").map(Number);
  return (
    isTradingDate(date) &&
    h * 60 + m >= 570 &&
    h * 60 + m < regularClose(date, legs)
  );
}
export function collectionDates(manifest: Manifest): string[] {
  // Each Eastern calendar day intersects at most two UTC storage partitions.
  const days = new Set(
    manifest.days
      .filter((d) => d.attempts > 0)
      .flatMap((d) => [d.date, shiftDate(d.date, -1)]),
  );
  return [...days].filter(isTradingDate).sort().reverse();
}
export function sourceDays(manifest: Manifest, date: string) {
  return manifest.days
    .filter((d) => d.date === date || d.date === shiftDate(date, 1))
    .sort((a, b) => a.date.localeCompare(b.date));
}
export function easternIndex(indexes: Index[], date: string): Index {
  const instruments = new Map<string, Instrument>();
  for (const index of indexes)
    for (const source of index.instruments) {
      const hourly = source.hourly.filter((h) => exchangeDate(h.at) === date);
      if (!hourly.length) continue;
      const prior = instruments.get(source.id);
      const hours = [...(prior?.hourly || []), ...hourly].sort((a, b) =>
        a.at.localeCompare(b.at),
      );
      instruments.set(source.id, {
        ...source,
        hourly: hours,
        attempts: hours.reduce((n, h) => n + h.attempts, 0),
        valid: hours.reduce((n, h) => n + h.valid, 0),
        nonempty: hours.reduce((n, h) => n + h.nonempty, 0),
        failed: hours.reduce((n, h) => n + h.attempts - h.valid, 0),
        details: [...(prior?.details || []), ...source.details],
      });
    }
  return { schema_version: 1, date, instruments: [...instruments.values()] };
}
export function legLabel(leg: Leg): string {
  return `${leg.BUY_SELL_FLAG || "?"} ${leg.Leg_Ratio ?? "?"} · ${leg.OSI_ROOT || "?"} · ${leg.STRIKE ?? "?"} · ${leg.EXPIRE_DATE || "?"}`;
}
export async function detailPathsForDate(
  paths: string[],
  date: string,
  read: (path: string) => Promise<Detail>,
): Promise<string[]> {
  // Parts are chronological. Find both boundaries without fetching every snapshot.
  const cache = new Map<string, Promise<Detail>>();
  const get = (path: string) => {
    if (!cache.has(path)) cache.set(path, read(path));
    return cache.get(path)!;
  };
  async function boundary(end: boolean) {
    let lo = 0,
      hi = paths.length;
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2),
        part = await get(paths[mid]);
      const samples = part.observations;
      if (!samples.length) throw new Error("Empty replay partition");
      const day = exchangeDate(samples[end ? 0 : samples.length - 1].at);
      if (end ? day <= date : day < date) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }
  const [first, last] = await Promise.all([boundary(false), boundary(true)]);
  return paths.slice(first, last);
}
