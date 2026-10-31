# Historical QSB and CPM dashboard design

Status: dashboard planning resumed on 2026-10-31. The user wants to discuss the
technology stack first, prioritizing static hosting on Cloudflare Pages/Workers
within its free tier. The architecture below is a proposal, not an approved
framework, retention policy, data publication policy, or deployment.

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
charts and availability analysis. Discuss hosting and stack before implementation.

## Static hosting feasibility, 2026-10-31

The frontend and predefined historical views can be static. The unbounded archive
should remain separate from frontend deployment assets. Heavy export, aggregation,
and restoration work runs on rn with its own bounded workspace; it must not compete
with or exhaust the collector's spool. Nothing here requires changing collection.

### Volume assumptions

The last production check observed 1,119 instruments. At a five-minute cadence,
that is about 87,282 observations over a hypothetical 6.5-hour populated session,
22 million across 252 such sessions, or 322,272 per day if that universe remains
populated for all 24 hours. Availability beyond regular hours is still empirical;
these scenarios do not impose a collection schedule.

`storage-measurement.json` records one segment: 4,453 responses occupied 25,165,824
SQLite bytes and 3,159,436 compressed archive bytes, roughly 710 archive bytes per
response. Applying that ratio to the 6.5-hour scenario gives roughly 62 MB per day
or 15.6 GB per 252 sessions, before reference/report overhead. The 24-hour scenario
is roughly 229 MB per day. These are extrapolations from one short development
sample, not full-day measurements, upper bounds, or derived-dashboard sizes.

One file per instrument/day would consume 20,000 deployment asset slots in about
18 populated days at this universe size. Packaging the entire growing history as
Pages/Workers deployment assets is therefore a poor default even if files are small.

### Proposed stack and data flow

- Frontend: TypeScript, React, Vite, client-side routing and charts; static output.
  Workers Static Assets is the first hosting choice; Pages is also viable.
- Exporter: extend the Python project with a separate bounded materialization job
  on rn, consuming sealed SQLite segments incrementally before/after archival.
- Browser serving format: compact compressed JSON partitions, with a small calendar
  and date catalog, daily cross-instrument summaries, and lazily loaded per-instrument
  observation/state files. Preserve exact prices as strings and all gaps, valid empty
  observations, and distinct collection/source clocks.
- Deduplicate repeated depth states in exports while retaining observation times
  and status. Do not deduplicate executions using row equality.
- Keep immutable versioned data objects separate from the short-lived manifest.
  Publish objects and verify them before atomically updating the manifest. Include
  schema version, checksums, coverage and export freshness.
- Raw archive: retain B2 as the canonical source. Never expose existing bucket
  credentials or assume the shared bucket may be made public.
- Serving storage option A: R2 Standard with an explicit byte budget (proposed 8 GB,
  leaving room below the account's shared free allowance) and retention/downsampling
  for derived data. Full archives stay in B2. Export size measurements determine
  the detail retention window; do not promise one yet.
- Serving storage option B: a dedicated B2 publication bucket through Cloudflare CDN
  for complete long-term detail. This avoids R2 history growth but retains B2 storage
  and request costs. A separate public bucket can serve static objects directly;
  private B2 requires a scoped server-side access path. The current archive bucket
  is not automatically a publication source.
- An optional thin Worker may stream allowlisted objects/authenticate requests.
  Its invocations, including Cache API hits, consume Worker request quota. Do not
  decompress archives, scan SQLite, or run analytical SQL in the free Worker.
- Defer D1 and browser DuckDB initially. Parquet plus DuckDB remains a later option
  for measured needs involving broad analytical queries. Fixed dashboard views can
  use precomputed series without shipping a query engine to every browser.

The first release can support the explorer, one instrument/day replay, precomputed
charts and coverage entirely from files. Arbitrary queries across every instrument
and all history need a different resource envelope. Sampled observations cannot
provide exchange event-by-event replay.

### Free-tier constraints checked against official documentation

- [Workers Static Assets billing](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/):
  static asset requests are free/unlimited; script invocations are separate.
- [Workers limits](https://developers.cloudflare.com/workers/platform/limits/):
  20,000 assets/version, 25 MiB/asset; Free scripts 100,000 requests/day, 10 ms CPU
  per request and 128 MB memory.
- [Pages limits](https://developers.cloudflare.com/pages/platform/limits/):
  20,000 files, 25 MiB/file and 500 builds/month on Free. Update data in object
  storage independently of frontend builds.
- [R2 pricing](https://developers.cloudflare.com/r2/pricing/): Standard includes
  10 GB-month storage, 1 million Class A and 10 million Class B operations/month;
  egress is free. This is an allowance with billable overages, not a spend cap.
  A byte cap does not bound read costs; use caching and operation monitoring.
- [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/) and
  [limits](https://developers.cloudflare.com/d1/platform/limits/): Free includes
  100,000 rows written/day, 5 million read/day, 500 MB/database, 5 GB/account.
  About 87,000 observations/day alone leaves little write allowance for levels,
  reference records and index maintenance. Avoid it as the full-history store.
- [Backblaze public B2 through Cloudflare](https://www.backblaze.com/docs/cloud-storage-deliver-public-backblaze-b2-content-through-cloudflare-cdn):
  documents a static CDN origin setup; JSON caching and CORS require configuration.

### Before selecting retention and implementation

Measure several full sessions, including inactive periods, for raw archives and
candidate dashboard exports separately: compressed bytes/day, unique states,
partition counts and sizes, largest instrument/day, and browser parse/memory cost.
Aim for small lazy-loaded partitions (initial target under 1 MB compressed) with
an explicit decoded-size ceiling and splitting for outliers. Measure initial load
and an instrument/day selection rather than downloading an entire day of all books.

The user selected recent detailed replay plus permanent summary charts on
2026-10-31. The exact recent-detail window remains to be sized from measured
exports. Expiration applies to derived serving data; raw archives remain in B2.
Public versus authenticated data access remains undecided and does not follow from
the existing public code/image decision. No Cloudflare or bucket changes were made
during this feasibility discussion.

### Repository layout recommendation, 2026-10-31

Keep the frontend in this repository under `dashboard/`, with its own package
manifest, lockfile, build output and Cloudflare deployment workflow. Keep the
Python collector/exporter in `qsb_capture/`. Document the versioned export contract
and maintain small synthetic fixtures shared by exporter validation and frontend
development; never commit captured history to frontend assets or source control.

This is an assistant recommendation, pending the user's choice. A single repository
allows exporter/schema/UI changes to be reviewed together. Frontend and collector
still build and deploy independently: dashboard-only changes should not publish a
new collector image, and collector-only changes should not deploy the frontend.
Keep Node dependencies and dashboard build artifacts out of the collector image.
Infrastructure configuration remains in the neighboring infra repository.

A separate `qsb-fe` repository becomes useful if frontend ownership/access or release
coordination diverges substantially. Separate repositories would currently add
schema compatibility coordination without changing Cloudflare runtime/storage costs.
