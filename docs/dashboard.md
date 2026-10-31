# Historical dashboard

The portable frontend is in `dashboard/`. It has its own Node lockfile, build,
credential-free CI and Cloudflare Worker. Account identity, domains, access rules,
R2 binding names and native branch-build configuration belong in private `infra`.
No deployment credential is stored in this public repository or its Actions.

## Local development

Use Python 3.12+ and Node 24:

```sh
python3.12 scripts/dashboard-demo.py
npm --prefix dashboard ci
npm --prefix dashboard run dev
```

Open `http://127.0.0.1:5173/?demo=1` for explicitly synthetic data. Demo fixtures
are generated through the real exporter, including failures, valid empties and
repeated trade rows. Without demo mode the app requests `/data/manifest.json`;
an absent publication produces a clear unavailable state, never fabricated history.

```sh
python3.12 -m unittest discover -s tests -v
npm --prefix dashboard test
npm --prefix dashboard run build
```

The browser smoke test expects the preview server on port 4173 (`npm run preview`
inside `dashboard/`); run `npm --prefix dashboard run test:browser` in another shell.

## Export and data contract

```sh
python3.12 -m qsb_capture.dashboard \
  --segments /path/to/complete-date/*.sealed.sqlite3 \
  --date 2026-10-29 --output /path/to/derived \
  --detail-days 14 --max-output-bytes 536870912 \
  --max-workspace-bytes 268435456 --min-free-bytes 2147483648
```

Rebuild a **UTC collection date from its complete set of source segments**, including
segments opened the preceding date if they cross midnight. This is deliberately
separate from reported trading dates. Never pass a live/open database. Archives can
be restored with the collector's verified bounded restore command; originals remain
unchanged. The automated synchronizer handles archive discovery and serial restoration.

Schema version 1:

- `manifest.json`: export timestamp, detail window, and collection dates with paths,
  attempt/valid counts, instrument counts and replay availability.
- A daily index: instrument versions, definitions, attempt/valid/nonempty/failure counts,
  observed changes and hourly best-quote ranges and coverage. These summaries persist.
- Each detail partition: at most 256 observations, timestamped collection attempts,
  source time, reported trading date, HTTP/status/error, referenced depth-state dictionary,
  and ordered trade-window dictionary. Decimal prices/strikes remain strings. Failed
  requests resolve instrument definitions only from their own sweep reference.

Immutable objects are named by SHA-256 of their compressed bytes. The manifest changes
only after referenced objects exist. Documents have an 8 MiB decoded ceiling; the
browser enforces it while streaming. Parts that exceed the ceiling fail export rather
than publish incomplete data. Repeated states share definitions; observations and trade
row multiplicity remain intact. Gaps and valid empties do not carry previous quotes.

Hourly ranges describe observed best bids/asks, not OHLC market bars or continuous
liquidity. SVG chart coordinates use floating point for display; stored decimal strings
remain exact. The third quote field has no assumed meaning. Trade windows are not
combined into a deduplicated execution tape.

## Automated export and publication

`python -m qsb_capture.dashboard_sync --config /private/dashboard.json --output /data`
reads bucket/prefix-restricted B2 credentials via the collector config, lists immutable
archives, and rebuilds only dates whose input fingerprint or detail eligibility changed.
It restores one archive at a time, preserving summaries across runs. It uses a separate
R2 Object Read & Write credential, restricted to a dedicated private serving bucket.
The Worker receives a read binding; browsers never receive credentials.

The private config supplies:

```json
{
  "archive_config": "/run/config/archive.json",
  "r2_endpoint": "https://YOUR_ACCOUNT_ID.r2.cloudflarestorage.com",
  "r2_bucket": "your-dashboard-bucket",
  "r2_credentials_file": "/run/secrets/r2.json",
  "detail_days": 14,
  "local_bytes": 1073741824,
  "output_bytes": 536870912,
  "workspace_bytes": 268435456,
  "max_segment_bytes": 33554432,
  "min_free_bytes": 2147483648,
  "remote_bytes": 8000000000
}
```

The R2 credential file contains `access_key_id` and `secret_access_key`, mode
0400/0440/0600. Infra supplies real values and schedules the job. Local output,
SQLite workspace, archive input/restore and control reserves fit inside the explicit
local budget. Existing collector spool usage is independent. The filesystem free-space
floor still applies. Disk exhaustion pauses exports, retaining the last published data.
The daily staging SQLite database has a hard page cap. No permanent growing raw index
or downloaded archive cache is introduced.

New objects are uploaded and fully downloaded for SHA-256 verification before publishing
the manifest. Unreferenced remote objects remain for 24 hours for cached manifests/open
tabs, then may be deleted from this dedicated derived bucket. Unexpected bucket keys
fail closed. Raw B2 archives are never deleted. R2 admission counts all objects including
unreachable objects, and reserves the new manifest before writes. An interrupted run
can leave harmless unreachable objects; if the cap prevents progress, review/reclaim only
unreferenced derived objects after their grace period before rerunning.

Permanent summaries still grow. If the output/index/control budgets are eventually
reached, exports stop; increase an explicit budget or introduce a coarser summary tier.
The byte cap is not an R2 request/spend cap: monitor Class A/B operations and the Worker's
100,000 daily request allowance. Every `/data/` request invokes the Worker, including
cache/conditional responses. Static JS/CSS/demo files use static-asset serving directly.

## Deployment boundary

The generic `wrangler.json` closes workers.dev and preview URLs. It contains no account,
domain, production bucket or R2 credential. Private infra injects DATA bindings and
maintains the environment mapping. Native Workers Builds deploy the development branch
and production branch separately; GitHub Actions only validates the public source.
Frontend deployment never uploads historical data. The export job publishes data without
rebuilding the frontend or restarting collection. Access applies to both assets and data.
