# Production collection and storage

## Deployment

Use the public linux/amd64 image `ghcr.io/yogenpro/qsb`. Production deployment
should select an immutable **registry manifest digest**, for example:

```
ghcr.io/yogenpro/qsb:v0.2.0@sha256:MANIFEST_DIGEST
```

The publisher records the digest in its GitHub Actions summary. Do not confuse
this with a local Docker config ID from the optional archive builder. Verify that
the image can be pulled anonymously; a public source repository does not itself
make a GHCR package public. No registry login belongs on the collection host.

The host needs Docker/Compose, outbound HTTPS and enough space for the fixed spool
budget plus image layers and rotated logs. The example deploys no network listener,
Docker socket, host network, tunnel or firewall rule. It runs uid/gid 65532,
drops all capabilities, uses a read-only root, and limits CPU, RAM and PIDs.

Provision:

- `/var/lib/qsb-capture`: uid/gid 65532, mode 0700, dedicated single-writer spool.
- `/opt/qsb-capture/config.json`: a copy of `config/example.json` with the actual
  Backblaze bucket, exact prefix and matching region/endpoint.
- `/etc/qsb-capture/b2.json`: root:65532 mode 0440, inside a root:root 0700 parent.
  Mount only the file read-only. Do not supply keys via Compose environment.

Use the deployment procedure appropriate for your infrastructure:

```sh
export QSB_IMAGE=ghcr.io/yogenpro/qsb:v0.2.0@sha256:MANIFEST_DIGEST
docker compose -f deploy/compose.yaml config --quiet
docker compose -f deploy/compose.yaml pull
docker compose -f deploy/compose.yaml up -d
```

Docker JSON logs are limited to three 10 MiB files. `/tmp` is a 16 MiB RAM
filesystem. The example limits the container to 384 MiB RAM, one CPU and 64 PIDs.

## Disk budget and outage behavior

The default **2 GiB cap** covers the entire `/data` tree: databases, rollback
journals, pending bundles, compression/restore temporaries, health and availability
state. An independent **2 GiB filesystem free-space floor** protects host capacity.
The application charges the larger of file length and allocated blocks, including
directories. The spool must not have symlinks or other writers.

Segments have a SQLite `max_page_count` of 32 MiB and 4096-byte pages. Every
transaction reserves DB/journal growth, compression workspace and bounded control
files. Responses are limited to 2 MiB; oversized responses retain an explicitly
marked prefix and are not presented as complete captures. Metadata is bounded to
128 KiB and individual control files to 1 MiB. Admission pauses conservatively
before the hard ceiling. Old unarchived data is never evicted to admit new data.

During an object-store outage, keep at most one compressed pending duplicate,
retain all source segments and retry maintenance every minute. Collection resumes
when verified uploads free space. Sustained failures can cause explicit collection
gaps, rather than consume the rest of the host disk. There is no unbounded local
manifest, receipt directory or per-sweep JSON index.

Image layers, host logs and unrelated services are outside the spool budget.
A filesystem/project quota can add kernel enforcement. No application can reserve
free space against unrelated concurrent writers; use a dedicated spool and keep
log/image cleanup under host management.

## Segment format

Schema: `qsb_capture/schema.sql`, version 1. Each segment independently contains:

- Original HTTP entity bytes in content-addressed `bodies`, plus attempt metadata,
  request/response times, status, headers and parse/truncation errors in `responses`.
- Versioned instrument definitions, ordered legs and reference membership. Reference
  context is embedded in every segment needing it; unioning segments must deduplicate
  repeated reference response IDs.
- Distinct depth states and source-ordered quote levels; distinct displayed trade
  windows and all source-ordered trade entries; every successful observation.
- Sweep coverage/availability reports, including failed-reference and empty-universe
  observations. Empty valid data is distinct from transport/validation failure.

Prices, strikes, quantities and ratios are exact strings. Use Decimal, or validate
precision before casting to an analytical DECIMAL type. Raw bodies preserve numeric
spellings. Preserve source time strings and their nanoseconds separately from known
UTC request instants. Instrument identity includes the reported trading date and
definition; identical trade rows are not automatically deduplicated executions.

Segments seal after 30 minutes or around 75% of the 32 MiB maximum. Restarts recover
rollback journals, check the open database and seal it before new collection.
Cold segments package after 15 minutes without explicit export access; storage
pressure bypasses that wait. Explicit offline export updates mtime; filesystem
atime is not used. Collection and offline operations share one writer lock.

## Archival verification

Object keys have the form:

```
<configured-prefix>/segments/YYYY/MM/DD/<segment>-<archive-sha256>.tar.gz
```

Each archive contains exactly `manifest.json` and `segment.sqlite3`. The manifest
records schema, database hash/size, counts and observed time bounds. Packaging
checks that the archive database matches its sealed source, including on retry.
Corrupt pending archives fail closed and preserve source data.

The uploader uses streaming S3 SigV4, explicit sizes, payload hashes and SSE-B2.
It checks a deterministic key before PUT; an existing object is fully verified
instead of blindly overwritten. Uploads are followed by a complete fresh GET,
using a returned version ID when available. Only an exact length and SHA-256 match
allows a bounded last-upload receipt and then local deletion of bundle/source.
No cleanup relies solely on HEAD or client-supplied object metadata.

No remote deletion, bucket lifecycle change, credential creation or key rotation
is automated. Remote history has indefinite retention and grows with collection;
monitor it and review a retention policy separately. A measured sample compressed
24 MiB of SQLite to about 3 MiB; activity, error payloads and symbol counts can
change this substantially. The disk cap does not rely on a compression forecast.

## Credentials

Provision a dedicated Backblaze file-only key, restricted to exactly one configured
bucket and the exact configured prefix including its trailing slash. File read and
write are required. File listing/bucket-name discovery are allowed. Broad prefixes,
multiple buckets, administration and bucket-settings/lifecycle writes are refused.
Do not reuse another service's key or substitute a master key.

```json
{"application_key_id":"DEDICATED_KEY_ID","application_key":"DEDICATED_KEY"}
```

The runtime reads values directly into memory, rejects symlinks/world access and
accepts 0400/0600 or 0440 modes. Native B2 v4 authorization verifies bucket/prefix,
capabilities and S3 endpoint before upload; scope is refreshed hourly. Restart the
container after rotating its mounted credential file. Never place storage keys in
GitHub Actions, the image, source control, command-line arguments or logs.

References: [Backblaze PUT](https://www.backblaze.com/apidocs/s3-put-object),
[B2 authorization](https://www.backblaze.com/apidocs/b2-authorize-account),
[AWS SigV4](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv-create-signed-request.html).

## Operation and recovery

```sh
docker compose -f deploy/compose.yaml exec collector python -m qsb_capture status
docker compose -f deploy/compose.yaml logs --tail 50 collector
docker compose -f deploy/compose.yaml stop collector
docker compose -f deploy/compose.yaml run --rm collector maintain
```

Status reports actual/capped bytes, capture health, archive health and last verified
upload. Stale heartbeats, storage pauses, archive failures and source failures
are visible. Docker does not restart a merely unhealthy container; configure host
monitoring accordingly. `restart: unless-stopped` handles process failures.

Stop collection before import/export/restore/forced maintenance. Legacy import
reads a consistent SQLite snapshot, checks original body hashes and preserves its
source. Import IDs already present locally are skipped; remote-evicted segments
are not searched for duplicates, so do not reimport an archived old source casually.

```sh
python3.12 -m qsb_capture import-legacy --config config/production.json \
  --output ./data --source /path/to/legacy
```

Explicit restore occupies the same capped spool:

```sh
docker compose -f deploy/compose.yaml run --rm collector restore \
  --object-key 'PREFIX/segments/YYYY/MM/DD/SEGMENT-SHA.tar.gz' \
  --object-bytes SIZE --sha256 SHA
```

Restore reserves space, bounds the download, validates both archive members and
the database hash, and runs SQLite integrity checking before exposing
`/data/restored.sqlite3`. It never extracts tar-supplied paths directly. Move/remove
an existing explicit restore before another. Query sealed/restored databases with
SQLite; raw payloads join through `responses.body_sha256` to `bodies.sha256`.
`export --segment /data/...` emits response metadata as JSONL. A future historical
query layer can derive Parquet/DuckDB data without affecting collection.

## Builds and testing

GitHub Actions builds the pinned Dockerfile, runs credential-free tests, and
exercises storage/archive/restore inside the real container without network or a
writable root. Passing main/version-tag builds publish public-source labels,
revision labels, provenance and SBOM to GHCR; no host deployment is part of CI.

`python3.12 scripts/build-image.py` is an optional daemon-free archive builder.
It validates the locked manifest/config, compressed/uncompressed layer hashes and
source allowlist. `--resolve-base` deliberately updates the lock; update the
Dockerfile digest together. Neither captures nor deployment secrets enter the image.
