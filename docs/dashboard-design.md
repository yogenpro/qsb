# Historical QSB and CPM dashboard design

Status: deferred at the user's request on 2026-10-29. This records the proposed
experience; the current implementation scope is collection, bounded storage,
archival, and container packaging.

## Data flow

Cboe endpoints → raw collection → immutable raw archive and normalized records →
query API → historical dashboard. Derived query data must be rebuildable from raw
captures. Collection runs independently of dashboard availability or queries.

The initial proposal used a SQLite capture index plus partitioned Parquet queried
with DuckDB. The bounded production collector uses self-contained SQLite segments
for both raw and normalized data; these can later feed Parquet exports. This avoids
an indefinitely growing local index and keeps each archived segment independently
queryable and restorable. Analytical columnar files are a later materialization.

## Records and identity

- Capture attempts: every request, retry, response status, collection start/end,
  error, original headers, and unchanged response bytes.
- Reference snapshots: reported trading date and universe membership at observation.
- Instrument versions: source type, source symbol, reported date and definition hash.
  Never assume IDs identify the same economic instrument across dates.
- Ordered legs: OSI symbol, root, expiry, strike, original signed ratio and side.
  Captures include two-, four-, and eight-leg instruments.
- Book observations: the actual sample time, source time string, reference association,
  distinct depth state, distinct trade window, and coverage/validation state.
- Quote levels: source order, side, quantity, exact decimal price, and the third
  quote field preserved without assigning an unsupported meaning.
- Trade-window entries: source order, original time string (including nanoseconds),
  size and exact decimal price. Identical rows may be distinct trades. Without a
  source event ID, equality is insufficient to deduplicate executions.
- Availability: valid responses, empty depth, nonempty depth, quote content changes,
  trade-window changes, source-clock changes, and transport/validation failures.

UTC request start/end, reported trading date and source time are separate clocks.
Do not assign dates/timezones to source-only time strings without evidence. Preserve
raw numeric spellings and use decimal arithmetic for prices, strikes and analytics.
Keep all observations even when they refer to unchanged states. Empty valid books
clear observed depth; failed captures create gaps.

## Views

1. Instrument explorer: date, QSB/CPM, subtype, root, leg expirations, observed
   liquidity and capture coverage. Open the versioned leg definition with an instrument.
2. Historical book replay: time scrubber, previous/next observation, depth ladder,
   displayed trade window, raw response download, sample age and capture status.
3. Price and liquidity history: best bid/ask, spread, sizes and depth over time.
   No implied continuous quotes across missing observations. Carry-forward is explicit,
   age-limited and visibly distinct from an actual sample.
4. Availability and coverage: separate tracks for endpoint success, nonempty depth,
   quote changes, trade-window changes and source timestamp progression. Report daily
   first/last observations and bracket transitions by the surrounding samples.

Sweeps contain samples minutes apart. Cross-instrument views use per-instrument
as-of observations with visible ages; never present a sweep as simultaneous.
Charts preserve gaps and display valid empties. Archive access must use a bounded
restore cache and cannot cause collection storage to exceed its cap.

## Implementation order

Normalize/backfill existing captures, then instrument explorer and replay, then
charts and availability analysis. Publish no dashboard in the current phase.
