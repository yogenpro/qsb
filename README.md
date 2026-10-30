# QSB capture

Capture the public Cboe QSB and CPM book viewer continuously, preserve original
response bytes, normalize historical observations, and archive cold data to
Backblaze B2. Local storage has a fixed budget: an upload outage pauses collection
before the disk cap is exhausted, without discarding unarchived data.

- **2 GiB default spool cap** and **2 GiB filesystem free-space floor**.
- Self-contained SQLite segments with raw bodies, exact decimal strings,
  versioned instrument legs, quote states, displayed trade windows and observations.
- Compressed archives; complete object read-back and checksum verification before
  local deletion. No remote expiry or lifecycle changes.
- Standard-library Python 3.10+ application; nonroot, read-only Docker image.
- QSB and CPM share one reference endpoint; all instruments are sampled by default
  every five minutes, with five requests per second aggregate.

[![Image workflow](https://github.com/yogenpro/qsb/actions/workflows/image.yml/badge.svg)](https://github.com/yogenpro/qsb/actions/workflows/image.yml)

## Container

Images are published at **`ghcr.io/yogenpro/qsb`** for linux/amd64. Release tags are
`v0.2.0`, with `sha-<full-commit>` tags for exact source revisions. Use a published
manifest digest in production; `main` and `latest` are moving convenience tags.

```sh
cp config/example.json config/production.json
# Set the destination bucket/prefix/region and provision the dedicated key file.
# The bucket in the example is deliberately a placeholder.
export QSB_IMAGE=ghcr.io/yogenpro/qsb:v0.2.0
docker compose -f deploy/compose.yaml up -d
```

The Compose example expects a host configuration at
`/opt/qsb-capture/config.json`, a credential file at `/etc/qsb-capture/b2.json`,
and an application-owned spool at `/var/lib/qsb-capture`. See the
[production runbook](docs/production.md) for permissions, immutable deployment
references, capacity behavior and recovery. It publishes no ports and does not
need a Docker socket or registry credential for a public image.

First-time GHCR publication can require changing the package visibility to Public
in GitHub's package settings. Anonymous pull verification is required before
calling an image publicly available.

## Development

```sh
python3.12 -m unittest discover -s tests -v
python3.12 -m qsb_capture run --config config/production.json --output ./data
python3.12 -m qsb_capture status --config config/production.json --output ./data
```

Tests use small synthetic fixtures shaped like the published schema. Captured
market data, website assets, local configuration and credentials are not tracked.
The legacy unbounded collection CLI is disabled.

The Dockerfile pins its official Python base by digest. The optional
`scripts/build-image.py` helper can produce a Docker-loadable image archive
without a Docker daemon; it verifies every base layer and allowlists source files.
Its receipt is a local Docker config ID. **Registry manifest digests from GHCR are
the references to use for production pulls.**

## Publishing

The SHA-pinned GitHub Actions workflow runs unit/integration tests, builds the
Dockerfile and exercises the container with no network, a read-only root,
nonroot user and bounded tmpfs. Only passing pushes to `main` and version tags on
`main` can publish to GHCR. Pull requests receive no package-write permission.
The workflow uses the repository's ephemeral `GITHUB_TOKEN`; it needs no personal
access token, storage key or production access. Published images include source
revision labels, provenance and an SBOM.

For a release, update `pyproject.toml`, push the version commit to main and create
the matching `vX.Y.Z` tag. Tags are validated against the project version and main
ancestry. Infrastructure can independently select and pin the resulting public
image digest; publishing never deploys to a host.

## Data semantics

The public viewer exposes snapshots and a bounded recent-trade window, not a
complete exchange event feed. Samples in a sweep are not simultaneous. Identical
trade rows cannot safely be assumed to be duplicate executions. Preserve source
time strings separately from UTC request times; decimal prices must be calculated
with decimal arithmetic. Empty valid books and failed requests are distinct.

Collection runs 24/7 to measure actual availability. Successful responses,
nonempty depth, quote changes, trade-window changes and source-clock changes are
recorded separately. An advancing server timestamp alone does not prove trading.
The [historical dashboard design](docs/dashboard-design.md) is deferred.

Source viewer: [Cboe quoted spread book reference data](https://www.cboe.com/markets/us/options/trading/quoted-spread-book/reference-data).
