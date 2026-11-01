import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  demo,
  load,
  time,
  percent,
  price,
  type Manifest,
  type Index,
  type Instrument,
  type Detail,
  type Observation,
  type Level,
} from "./data";
import { hourlySlots } from "./hours";
import { expirationDates, filterInstruments, strategyLabel } from "./filters";
import {
  collectionDates,
  detailPathsForDate,
  easternIndex,
  exchangeClock,
  exchangeDate,
  exchangeZone,
  regularMinute,
  sourceDays,
} from "./exchange";
import "./style.css";

function Stat({
  label,
  value,
  note,
}: {
  label: string;
  value: React.ReactNode;
  note: string;
}) {
  return (
    <div className="stat">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </div>
  );
}
function Chart({ instrument }: { instrument: Instrument }) {
  const points = hourlySlots(instrument.hourly);
  const values = points
    .flatMap((p) => (p ? [p.bid_min, p.bid_max, p.ask_min, p.ask_max] : []))
    .filter((v) => v !== null)
    .map(Number)
    .filter(Number.isFinite);
  if (!values.length)
    return (
      <div className="chart-empty">
        No quoted prices in these samples.
        <span>Empty books and failed captures remain visible in coverage.</span>
      </div>
    );
  const lo = Math.min(...values),
    hi = Math.max(...values),
    padding = (hi - lo) * 0.15 || 1;
  const y = (n: number) =>
    142 - ((n - lo + padding) / (hi - lo + 2 * padding)) * 116;
  const x = (i: number) => 60 + ((i + 0.5) * 680) / points.length;
  const first = Date.parse(instrument.hourly[0].at);
  const bands: { start: number; end: number; regular: boolean }[] = [];
  for (let i = 0; i < points.length * 4; i++) {
    const regular = regularMinute(
      first + i * 900000,
      instrument.definition.Leg_Data || [],
    );
    const prior = bands.at(-1);
    if (prior?.regular === regular) prior.end = i + 1;
    else bands.push({ start: i, end: i + 1, regular });
  }
  return (
    <svg
      className="chart"
      viewBox="0 0 800 190"
      role="img"
      aria-label="Hourly observed best bid and ask ranges in Eastern Time, shaded by trading session"
    >
      {bands.map((b) => (
        <rect
          key={b.start}
          x={60 + (b.start * 680) / (points.length * 4)}
          y="10"
          width={((b.end - b.start) * 680) / (points.length * 4)}
          height="145"
          className={b.regular ? "session-regular" : "session-extended"}
        >
          <title>
            {exchangeClock(first + b.start * 900000)}–
            {exchangeClock(first + b.end * 900000)} ET ·{" "}
            {b.regular
              ? "Regular trading hours"
              : "Extended / outside regular hours"}
          </title>
        </rect>
      ))}
      {[lo, lo + (hi - lo) / 2, hi].map((v, i) => (
        <g key={i}>
          <line x1="58" x2="755" y1={y(v)} y2={y(v)} className="gridline" />
          <text x="48" y={y(v) + 4} textAnchor="end">
            {v.toFixed(2)}
          </text>
        </g>
      ))}
      {points.map((p, i) =>
        p ? (
          <g key={p.at}>
            <title>
              {time(p.at)}: {p.valid}/{p.attempts} valid; bid {price(p.bid_min)}
              –{price(p.bid_max)}, ask {price(p.ask_min)}–{price(p.ask_max)}
            </title>
            {p.bid_min !== null && (
              <line
                className="bid-range"
                x1={x(i) - 3}
                x2={x(i) - 3}
                y1={y(Number(p.bid_max))}
                y2={y(Number(p.bid_min))}
              />
            )}
            {p.ask_min !== null && (
              <line
                className="ask-range"
                x1={x(i) + 3}
                x2={x(i) + 3}
                y1={y(Number(p.ask_max))}
                y2={y(Number(p.ask_min))}
              />
            )}
          </g>
        ) : null,
      )}
      {Array.from({ length: points.length + 1 }, (_, i) =>
        i % (points.length > 14 ? 2 : 1) === 0 || i === points.length ? (
          <text
            key={i}
            x={60 + (i * 680) / points.length}
            y="175"
            textAnchor="middle"
          >
            {exchangeClock(first + i * 3600000)}
          </text>
        ) : null,
      )}
    </svg>
  );
}
function Ladder({ levels, side }: { levels: Level[]; side: "bid" | "ask" }) {
  return (
    <div className={`ladder ${side}`}>
      <div className="ladder-title">
        <span>{side === "bid" ? "Bids" : "Asks"}</span>
        <span>{levels.length} levels</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Size</th>
            <th>Price</th>
            <th>Extra</th>
          </tr>
        </thead>
        <tbody>
          {levels.map((row, i) => (
            <tr key={i}>
              <td>{String(row[0])}</td>
              <td>{price(row[1])}</td>
              <td title={JSON.stringify(row[2])}>
                {typeof row[2] === "string" ? row[2] : JSON.stringify(row[2])}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!levels.length && (
        <p className="muted table-empty">
          No displayed {side === "bid" ? "bids" : "asks"}
        </p>
      )}
    </div>
  );
}
function Replay({
  instrument,
  date,
}: {
  instrument: Instrument;
  date: string;
}) {
  const [part, setPart] = useState(0),
    [parts, setParts] = useState<string[] | null>(null),
    [detail, setDetail] = useState<Detail | null>(null),
    [position, setPosition] = useState(0),
    [playing, setPlaying] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    setPart(0);
    setPlaying(false);
    setParts(null);
    setError("");
    const c = new AbortController();
    detailPathsForDate(instrument.details, date, (path) =>
      load<Detail>(path, c.signal),
    )
      .then(setParts)
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => c.abort();
  }, [instrument, date]);
  useEffect(() => {
    setDetail(null);
    setError("");
    setPosition(0);
    setPlaying(false);
    if (!parts?.[part]) return;
    const controller = new AbortController();
    load<Detail>(parts[part], controller.signal)
      .then((value) => {
        if (value.instrument_id !== instrument.id)
          throw new Error("Instrument data mismatch");
        setDetail({
          ...value,
          observations: value.observations.filter(
            (o) => exchangeDate(o.at) === date,
          ),
        });
      })
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => controller.abort();
  }, [instrument, parts, part, date]);
  useEffect(() => {
    if (!playing || !detail) return;
    const id = setInterval(
      () =>
        setPosition((p) => {
          if (p >= detail.observations.length - 1) {
            setPlaying(false);
            return p;
          }
          return p + 1;
        }),
      650,
    );
    return () => clearInterval(id);
  }, [playing, detail]);
  if (!instrument.details.length)
    return (
      <section className="panel">
        <div className="section-heading">
          <h2>Book replay</h2>
          <span className="badge">Summary retained</span>
        </div>
        <p className="muted">
          Detailed replay for this date has expired. Hourly summaries and
          collection coverage remain available.
        </p>
      </section>
    );
  const observation: Observation | undefined = detail?.observations[position];
  const state = observation?.depth_id
    ? detail?.states[observation.depth_id]
    : null;
  const trades = observation?.trades_id
    ? detail?.trade_windows[observation.trades_id] || []
    : [];
  return (
    <section className="panel replay">
      <div className="section-heading">
        <div>
          <span className="eyebrow">OBSERVED SNAPSHOTS</span>
          <h2>Book replay</h2>
        </div>
        <label className="part-select">
          Partition
          <select
            value={part}
            onChange={(e) => setPart(Number(e.target.value))}
          >
            {(parts || []).map((_, i) => (
              <option key={i} value={i}>
                {i + 1} / {parts?.length}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!detail && !error && <p role="status">Loading observations…</p>}
      {observation && (
        <>
          <div className="replay-timing">
            <div>
              <span className="muted">Collected · Eastern Time</span>
              <strong>{time(observation.at)}</strong>
            </div>
            <span className={`badge ${observation.status}`}>
              {observation.status === "nonempty"
                ? "Quoted book"
                : observation.status === "empty"
                  ? "Valid empty book"
                  : "Capture failed"}
            </span>
            <div className="source-time">
              <span className="muted">Source time · as published</span>
              <strong>{observation.source_time || "—"}</strong>
            </div>
          </div>
          <div className="scrubber">
            <button
              aria-label={playing ? "Pause replay" : "Play replay"}
              onClick={() => {
                if (position === detail!.observations.length - 1)
                  setPosition(0);
                setPlaying(!playing);
              }}
            >
              {playing ? "Ⅱ Pause" : "▶ Play"}
            </button>
            <button
              aria-label="Previous observation"
              disabled={position === 0}
              onClick={() => setPosition((p) => p - 1)}
            >
              ←
            </button>
            <input
              aria-label="Observation"
              type="range"
              min="0"
              max={detail!.observations.length - 1}
              value={position}
              onChange={(e) => {
                setPlaying(false);
                setPosition(Number(e.target.value));
              }}
            />
            <button
              aria-label="Next observation"
              disabled={position === detail!.observations.length - 1}
              onClick={() => setPosition((p) => p + 1)}
            >
              →
            </button>
            <span>
              {position + 1} / {detail!.observations.length}
            </span>
          </div>
          <p className="caption">
            Discrete samples. Missing captures are gaps; source time has no
            assumed timezone.
          </p>
          {observation.status === "failed" ? (
            <div className="failure">
              <h3>No book available for this attempt</h3>
              <p>
                HTTP {observation.http_status ?? "unavailable"} ·{" "}
                {observation.error || "Response could not be normalized."}
              </p>
              <p>The previous book is not carried forward.</p>
            </div>
          ) : (
            state && (
              <div className="book-grid">
                <Ladder levels={state[0]} side="bid" />
                <Ladder levels={state[1]} side="ask" />
              </div>
            )
          )}
          <details className="trades">
            <summary>
              Published trade window <span>{trades?.length || 0} entries</span>
            </summary>
            <p className="caption">
              A source window, not a deduplicated execution tape. Repeated rows
              are retained.
            </p>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Source time</th>
                    <th>Size</th>
                    <th>Price</th>
                  </tr>
                </thead>
                <tbody>
                  {trades?.map((t, i) => (
                    <tr key={i}>
                      <td>{String(t[0])}</td>
                      <td>{String(t[1])}</td>
                      <td>{price(String(t[2]))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!trades?.length && (
              <p className="muted">No entries in this sample.</p>
            )}
          </details>
        </>
      )}
    </section>
  );
}
function App() {
  const [manifest, setManifest] = useState<Manifest | null>(null),
    [dates, setDates] = useState<string[]>([]),
    [date, setDate] = useState(""),
    [index, setIndex] = useState<Index | null>(null),
    [error, setError] = useState(""),
    [query, setQuery] = useState(""),
    [type, setType] = useState("ALL"),
    [strategy, setStrategy] = useState("ALL"),
    [expiry, setExpiry] = useState("ALL"),
    [selected, setSelected] = useState(""),
    [page, setPage] = useState(0);
  useEffect(() => {
    const c = new AbortController();
    load<Manifest>("manifest.json", c.signal)
      .then(async (m) => {
        const nonempty = m.days
          .filter((d) => d.attempts > 0)
          .sort((a, b) => a.date.localeCompare(b.date));
        const edges = [
          ...new Set(
            [nonempty[0]?.path, nonempty.at(-1)?.path].filter(
              (p): p is string => !!p,
            ),
          ),
        ];
        const indexes = await Promise.all(
          edges.map((p) => load<Index>(p, c.signal)),
        );
        const bounds = indexes
          .flatMap((i) =>
            i.instruments.flatMap((s) =>
              s.hourly.map((h) => exchangeDate(h.at)),
            ),
          )
          .sort();
        const available = collectionDates(m).filter(
          (d) => d >= bounds[0] && d <= bounds.at(-1)!,
        );
        setManifest(m);
        setDates(available);
        setDate(available[0] || "");
      })
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => c.abort();
  }, []);
  useEffect(() => {
    if (!date || !manifest) return;
    setIndex(null);
    setSelected("");
    setError("");
    const c = new AbortController();
    Promise.all(
      sourceDays(manifest, date).map((d) => load<Index>(d.path, c.signal)),
    )
      .then((values) => setIndex(easternIndex(values, date)))
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => c.abort();
  }, [date, manifest]);
  const bookInstruments = useMemo(
    () =>
      (index?.instruments || []).filter(
        (i) => type === "ALL" || i.type === type,
      ),
    [index, type],
  );
  const strategies = useMemo(
    () => [...new Set(bookInstruments.map((i) => i.subtype))].sort(),
    [bookInstruments],
  );
  const expirations = useMemo(
    () =>
      [
        ...new Set(
          bookInstruments
            .filter((i) => strategy === "ALL" || i.subtype === strategy)
            .flatMap(expirationDates),
        ),
      ].sort(),
    [bookInstruments, strategy],
  );
  const filtered = useMemo(
    () =>
      filterInstruments(
        index?.instruments || [],
        type,
        strategy,
        expiry,
        query,
      ),
    [index, query, type, strategy, expiry],
  );
  useEffect(() => setPage(0), [query, type, strategy, expiry, date]);
  function clearFilters() {
    setQuery("");
    setType("ALL");
    setStrategy("ALL");
    setExpiry("ALL");
  }
  const instrument = filtered.find((i) => i.id === selected) || filtered[0];
  const totals = (index?.instruments || []).reduce(
    (a, i) => ({
      attempts: a.attempts + i.attempts,
      valid: a.valid + i.valid,
      nonempty: a.nonempty + i.nonempty,
    }),
    { attempts: 0, valid: 0, nonempty: 0 },
  );
  return (
    <>
      <header className="topbar">
        <a href="/" className="brand">
          <span className="brand-icon">q</span>QSB{" "}
          <span className="brand-slash">/</span> CPM
          <span className="brand-label">MARKET ARCHIVE</span>
        </a>
        <span className="topbar-note">
          Cboe book observations
          <span className="status-dot" />
        </span>
      </header>
      {demo && (
        <aside className="demo-banner">
          SYNTHETIC DEMO · Illustrative instruments and prices.{" "}
          <a href="/">Return to historical data →</a>
        </aside>
      )}
      <main>
        <div className="intro">
          <div>
            <span className="eyebrow">THE HISTORICAL RECORD</span>
            <h1>
              A closer look at
              <br />
              <em>the complex book.</em>
            </h1>
            <p>
              Explore QSB and CPM liquidity, one observed snapshot at a time.
            </p>
          </div>
          <div className="date-control">
            <label htmlFor="date">COLLECTION DATE · EASTERN TIME</label>
            <select
              id="date"
              value={date}
              onChange={(e) => {
                setDate(e.target.value);
                setStrategy("ALL");
                setExpiry("ALL");
              }}
              disabled={!dates.length}
            >
              {!dates.length && <option>No trading dates available</option>}
              {dates.map((d) => (
                <option key={d}>{d}</option>
              ))}
            </select>
            <span>Recent replay · lasting summaries</span>
          </div>
        </div>
        {error ? (
          <section className="panel empty-state" role="alert">
            <h2>Archive unavailable</h2>
            <p>{error}</p>
            <button onClick={() => location.reload()}>Try again</button>
            <a href="/?demo=1">Explore a labeled synthetic demo →</a>
          </section>
        ) : !manifest || (!index && date) ? (
          <section className="panel" role="status">
            Loading archive…
          </section>
        ) : !index ? (
          <section className="panel empty-state">
            <h2>The archive is ready for its first session</h2>
            <p>
              Published sessions will appear here as data becomes available.
            </p>
          </section>
        ) : (
          <>
            <section className="stats" aria-label="Session summary">
              <Stat
                label="INSTRUMENTS OBSERVED"
                value={index.instruments.length.toLocaleString()}
                note="Versioned definitions"
              />
              <Stat
                label="CAPTURE ATTEMPTS"
                value={totals.attempts.toLocaleString()}
                note="Includes retries and failures"
              />
              <Stat
                label="VALID RESPONSES"
                value={percent(totals.valid, totals.attempts)}
                note={`${(totals.attempts - totals.valid).toLocaleString()} failed attempts`}
              />
              <Stat
                label="QUOTED SAMPLES"
                value={percent(totals.nonempty, totals.valid)}
                note="At least one bid or ask"
              />
            </section>
            <div className="workspace">
              <aside className="panel explorer">
                <div className="section-heading">
                  <h2>Instruments</h2>
                  <span className="count">{filtered.length}</span>
                </div>
                <div
                  className="type-tabs"
                  role="group"
                  aria-label="Instrument type"
                >
                  {["ALL", "QSB", "CPM"].map((t) => (
                    <button
                      key={t}
                      aria-pressed={type === t}
                      onClick={() => {
                        setType(t);
                        setStrategy("ALL");
                        setExpiry("ALL");
                      }}
                    >
                      {t === "ALL" ? "All books" : t}
                    </button>
                  ))}
                </div>
                <input
                  className="search"
                  aria-label="Search instruments"
                  placeholder="Search symbol, strike, expiry…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                <div className="instrument-filters">
                  <label htmlFor="strategy">Strategy type</label>
                  <select
                    id="strategy"
                    value={strategy}
                    onChange={(e) => {
                      setStrategy(e.target.value);
                      setExpiry("ALL");
                    }}
                  >
                    <option value="ALL">All strategies</option>
                    {strategies.map((s) => (
                      <option key={s} value={s}>
                        {strategyLabel(s)}
                      </option>
                    ))}
                  </select>
                  <label htmlFor="expiry">Expiration date</label>
                  <select
                    id="expiry"
                    value={expiry}
                    onChange={(e) => setExpiry(e.target.value)}
                  >
                    <option value="ALL">All expirations</option>
                    {expirations.map((d) => (
                      <option key={d} value={d}>
                        {d}
                      </option>
                    ))}
                  </select>
                  <span className="caption">
                    Matches any included leg’s expiration.
                  </span>
                  <button
                    className="clear-filters"
                    onClick={clearFilters}
                    disabled={
                      !query &&
                      type === "ALL" &&
                      strategy === "ALL" &&
                      expiry === "ALL"
                    }
                  >
                    Clear filters
                  </button>
                </div>
                <div className="instrument-list">
                  {filtered.slice(page * 40, (page + 1) * 40).map((i) => (
                    <button
                      key={i.id}
                      className={`instrument ${instrument?.id === i.id ? "active" : ""}`}
                      onClick={() => setSelected(i.id)}
                    >
                      <div>
                        <strong>{i.symbol}</strong>
                        <span className="instrument-type">{i.type}</span>
                      </div>
                      <p>{strategyLabel(i.subtype)}</p>
                      <ul
                        className="instrument-legs"
                        aria-label={`${i.symbol} legs`}
                      >
                        {(i.definition.Leg_Data || []).map((leg, n) => (
                          <li key={n}>
                            <span>
                              {leg.BUY_SELL_FLAG || "?"} {leg.Leg_Ratio ?? "?"}{" "}
                              · {leg.OSI_ROOT || "?"} · {leg.STRIKE ?? "?"}
                            </span>
                            <span className="leg-expiry">
                              Exp {leg.EXPIRE_DATE || "?"}
                            </span>
                          </li>
                        ))}
                      </ul>
                      <div className="instrument-meta">
                        <span>
                          {[
                            ...new Set(
                              (i.definition.Leg_Data || []).map(
                                (l) => l.OSI_ROOT,
                              ),
                            ),
                          ]
                            .filter(Boolean)
                            .join(" / ") || "Unknown definition"}
                        </span>
                        <span>{percent(i.nonempty, i.valid)} quoted</span>
                      </div>
                    </button>
                  ))}
                  {!filtered.length && (
                    <p className="muted">No instruments match these filters.</p>
                  )}
                </div>
                {filtered.length > 40 && (
                  <div className="pagination">
                    <button
                      disabled={!page}
                      onClick={() => setPage((p) => p - 1)}
                    >
                      ←
                    </button>
                    <span>
                      {page + 1} / {Math.ceil(filtered.length / 40)}
                    </span>
                    <button
                      disabled={(page + 1) * 40 >= filtered.length}
                      onClick={() => setPage((p) => p + 1)}
                    >
                      →
                    </button>
                  </div>
                )}
              </aside>
              <div className="detail-column">
                {instrument ? (
                  <>
                    <section className="panel instrument-overview">
                      <div className="section-heading">
                        <div>
                          <span className="eyebrow">
                            {instrument.type} ·{" "}
                            {instrument.subtype.replaceAll("_", " ")}
                          </span>
                          <h2>{instrument.symbol}</h2>
                        </div>
                        <span className="badge">
                          {instrument.trading_date || "Unknown trading date"}
                        </span>
                      </div>
                      <p className="caption">
                        Definition version {instrument.id.slice(0, 10)} ·
                        Eastern collection date {date}
                      </p>
                      <details>
                        <summary>
                          Instrument legs{" "}
                          <span>
                            {instrument.definition.Leg_Data?.length || 0} legs
                          </span>
                        </summary>
                        <div className="table-scroll">
                          <table>
                            <thead>
                              <tr>
                                <th>Side / ratio</th>
                                <th>Root</th>
                                <th>Expiry</th>
                                <th>Strike</th>
                                <th>OSI symbol</th>
                              </tr>
                            </thead>
                            <tbody>
                              {instrument.definition.Leg_Data?.map((l, i) => (
                                <tr key={i}>
                                  <td>
                                    {l.BUY_SELL_FLAG} / {String(l.Leg_Ratio)}
                                  </td>
                                  <td>{l.OSI_ROOT}</td>
                                  <td>{l.EXPIRE_DATE}</td>
                                  <td>{String(l.STRIKE)}</td>
                                  <td>{l.OSI_Symbol}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </details>
                    </section>
                    <section className="panel">
                      <div className="section-heading">
                        <div>
                          <span className="eyebrow">
                            PERMANENT HOURLY SUMMARY
                          </span>
                          <h2>Observed quote ranges</h2>
                        </div>
                        <div className="legend">
                          <span className="bid-key">Bid</span>
                          <span className="ask-key">Ask</span>
                        </div>
                      </div>
                      <div className="chart-scroll">
                        <Chart instrument={instrument} />
                      </div>
                      <div
                        className="legend session-legend"
                        aria-label="Trading sessions"
                      >
                        <span className="regular-key">
                          Regular trading hours
                        </span>
                        <span className="extended-key">
                          Extended / outside regular hours
                        </span>
                      </div>
                      <p className="caption">
                        Hourly min–max of observed best quotes · Eastern Time
                        (EST/EDT). Shading follows the regular session,
                        including early closes. Outside-session observations may
                        contain retained quotes.
                      </p>
                      <div className="coverage">
                        <div className="section-heading">
                          <h3>Capture coverage</h3>
                          <span>
                            {percent(instrument.valid, instrument.attempts)}{" "}
                            valid
                          </span>
                        </div>
                        <div className="coverage-track">
                          {hourlySlots(instrument.hourly).map((h, i) =>
                            h ? (
                              <div
                                key={h.at}
                                title={`${time(h.at)}: ${h.valid}/${h.attempts} valid, ${h.nonempty} quoted`}
                                style={{
                                  background:
                                    h.valid === h.attempts
                                      ? "#237e6e"
                                      : "#b77037",
                                  opacity: 0.3 + (0.7 * h.valid) / h.attempts,
                                }}
                              />
                            ) : (
                              <div
                                key={`gap-${i}`}
                                className="coverage-gap"
                                title="No attempts recorded in this hour"
                              />
                            ),
                          )}
                        </div>
                        <div className="coverage-labels">
                          <span>
                            {instrument.valid} valid · {instrument.failed}{" "}
                            failed · {instrument.nonempty} quoted
                          </span>
                          <span>
                            {instrument.hourly.length} hours with capture
                            attempts
                          </span>
                        </div>
                      </div>
                    </section>
                    <Replay
                      key={`${date}-${instrument.id}`}
                      instrument={instrument}
                      date={date}
                    />
                  </>
                ) : (
                  <section className="panel">
                    <h2>No matching instruments</h2>
                    <p>Try another type, search or collection date.</p>
                  </section>
                )}
              </div>
            </div>
          </>
        )}
        <footer>
          <span>
            QSB / CPM <span className="footer-dot">·</span> Independent
            historical viewer
          </span>
          <p>
            Sampled public books, not an exchange event feed. Instruments in a
            sweep are observed at different times.
          </p>
          {manifest && (
            <p>
              Export updated{" "}
              {new Date(manifest.generated_at).toLocaleString("en-US", {
                timeZone: exchangeZone,
                timeZoneName: "short",
                hourCycle: "h23",
              })}{" "}
              · Detail window {manifest.detail_days} days
            </p>
          )}
        </footer>
      </main>
    </>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
