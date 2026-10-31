export interface Day {
  date: string;
  path: string;
  has_detail: boolean;
  attempts: number;
  valid: number;
  instruments: number;
}
export interface Manifest {
  schema_version: number;
  generated_at: string;
  detail_days: number;
  days: Day[];
}
export interface Hour {
  at: string;
  attempts: number;
  valid: number;
  nonempty: number;
  bid_min: string | null;
  bid_max: string | null;
  ask_min: string | null;
  ask_max: string | null;
}
export interface Leg {
  OSI_Symbol?: string;
  OSI_ROOT?: string;
  EXPIRE_DATE?: string;
  STRIKE?: string;
  Leg_Ratio?: string;
  BUY_SELL_FLAG?: string;
}
export interface Instrument {
  id: string;
  symbol: string;
  type: string;
  subtype: string;
  trading_date: string | null;
  definition: { Leg_Data?: Leg[] };
  attempts: number;
  valid: number;
  nonempty: number;
  failed: number;
  depth_changes: number;
  trade_window_changes: number;
  first: string;
  last: string;
  hourly: Hour[];
  details: string[];
}
export interface Index {
  schema_version: number;
  date: string;
  instruments: Instrument[];
}
export type Level = [string | number, string | number, unknown];
export interface Observation {
  id: string;
  at: string;
  started_at: string;
  source_time: string | null;
  trading_date: string | null;
  http_status: number | null;
  status: "nonempty" | "empty" | "failed";
  error: string | null;
  depth_id?: string;
  trades_id?: string;
  bid?: string | null;
  ask?: string | null;
}
export interface Detail {
  schema_version: number;
  instrument_id: string;
  observations: Observation[];
  states: Record<string, [Level[], Level[]]>;
  trade_windows: Record<string, Level[]>;
}
export const demo = new URLSearchParams(location.search).get("demo") === "1";
const base = demo ? "/demo-data/" : "/data/";
export async function load<T>(path: string, signal?: AbortSignal): Promise<T> {
  if (
    path !== "manifest.json" &&
    !/^objects\/[a-f0-9]{64}\.json(?:\.gz)?$/.test(path)
  )
    throw new Error("Invalid data path");
  // Bypass immutable responses cached before the Worker gzip pass-through fix.
  const response = await fetch(base + path + (path.endsWith(".gz") ? "?v=2" : ""), {
    signal,
  });
  if (!response.ok)
    throw new Error(
      response.status === 503
        ? "Historical data has not been published yet. Please check back shortly."
        : `Unable to load historical data (HTTP ${response.status}).`,
    );
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Empty data response");
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > 8 * 1024 * 1024) {
      await reader.cancel();
      throw new Error("Data partition exceeds the browser size limit.");
    }
    chunks.push(value);
  }
  const all = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    all.set(chunk, offset);
    offset += chunk.byteLength;
  }
  const value = JSON.parse(new TextDecoder().decode(all));
  if (value.schema_version !== 1)
    throw new Error("This data requires a newer dashboard version.");
  return value as T;
}
export function time(value: string | null | undefined) {
  return value
    ? new Date(value).toLocaleTimeString("en-GB", {
        timeZone: "UTC",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "—";
}
export function percent(a: number, b: number) {
  return b ? `${((100 * a) / b).toFixed(1)}%` : "—";
}
export function price(value: string | number | null | undefined) {
  return value == null ? "—" : String(value);
}
