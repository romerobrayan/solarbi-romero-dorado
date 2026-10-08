# 0001. Use PostgreSQL 16 with the TimescaleDB extension

- **Status:** Accepted
- **Date:** 2026-10-08
- **Phase:** 0

## Context

The assignment requires PostgreSQL. The data is time series: every inverter reports every
5 minutes. The simulator produces ~870 rows, but a real dataset of 4M+ rows arrives in Phase 6,
and Grafana must keep answering "power over the last N hours" queries interactively at that size.
Both consumers (Grafana and Power BI) must read the same governed database.

Options considered:

- **Plain `postgres:16`**: meets the requirement, but time-bucketed aggregations over millions of
  rows need hand-built partitioning and summary tables.
- **TimescaleDB on PostgreSQL 16**: still PostgreSQL (same wire protocol, SQL, drivers and Power
  BI connector), adds hypertables (automatic time partitioning), `time_bucket()`, continuous
  aggregates and native compression.
- **A separate analytical engine** (ClickHouse, DuckDB): fast, but violates the "PostgreSQL"
  requirement and splits the governed dataset in two.

## Decision

Run the official image `timescale/timescaledb:2.30.2-pg16`, pinned to an exact tag.

- The **non-`-oss`** variant is used on purpose: the `-oss` images exclude the
  community-licensed features (continuous aggregates, compression) that Phase 6 relies on.
- `sql/init/` is mounted over `/docker-entrypoint-initdb.d/`. This hides the image's own init
  scripts, so `00_schemas.sql` creates the extension itself and server settings are declared
  explicitly in `docker-compose.yml` (`command: postgres -c ...`); telemetry is turned off there.
- Tables are created as normal PostgreSQL tables first; `silver.lectura_5min` is converted to a
  hypertable when the ETL lands (Phase 2) or, at the latest, before loading the real dataset.

## Consequences

- Everything that works with PostgreSQL keeps working: psycopg, Grafana's PostgreSQL datasource
  (with `timescaledb: true` enabling `$__timeGroup` macros on `time_bucket`), Power BI's
  PostgreSQL connector.
- Phase 6 can add continuous aggregates (e.g. hourly power per inverter) for Grafana instead of
  scanning raw readings on every refresh.
- `timescaledb-tune` no longer runs automatically. Memory settings (`shared_buffers`,
  `work_mem`, …) must be set explicitly in `docker-compose.yml` before the 4M-row load; this is
  intentional, because auto-tuning would give different settings on every machine.
- The community features are under the Timescale License: free to use for this project, but they
  cannot be offered as a hosted database service. Not a concern for a university project.
- Upgrading means changing the tag deliberately; a TimescaleDB upgrade on an existing volume also
  requires `ALTER EXTENSION timescaledb UPDATE`.

## Update — Phase 1 (2026-10-08): hypertable settings

`silver.lectura_5min` became a hypertable in migration `0004_silver_lectura_5min.sql`, earlier than
planned, because converting an empty table is free while converting a loaded one rewrites it.

- **Partitioning:** by `ts` only, **7-day chunks**. Sizing for the ~4M-row dataset: at one reading
  every 5 minutes, 4M rows are ~13 900 device-days (e.g. 20 inverters over ~2 years, or 50 over
  ~9 months). A row with its two indexes is roughly 150–200 bytes, so a 7-day chunk holds about
  7 MB for 20 inverters and 17 MB for 50. TimescaleDB recommends that the most recent chunks (data
  and indexes) fit in about 25% of memory; with the default 128 MB `shared_buffers` that is ~32 MB,
  and 7-day chunks stay below it. Two years of data are ~105 chunks, few enough for fast planning.
  Grafana's usual ranges (last 24 hours, last 7 days) touch one or two chunks.
- If the real dataset has many more devices, `set_chunk_time_interval()` changes the interval for
  new chunks without a reload; revisit in Phase 6 together with explicit memory settings.
- **No space partitioning** by `dispositivo_id`: it only pays off with multiple disks or nodes.
- **Indexes:** the default `ts DESC` index (time-range queries across devices) plus the unique
  natural key `(dispositivo_id, ts DESC)`, which also serves "latest readings of one device".
- **First continuous aggregate (Phase 6):** hourly power per device
  (`time_bucket('1 hour', ts)`, `dispositivo_id`, avg/max `p_ac_kw`, reading count). It backs
  Grafana's power panels over long ranges. Daily energy stays in `dwh.fact_energia_dia`, because
  its day is the local day (ADR 0004).
- Compression of chunks older than ~30 days is the next step once the real dataset is loaded.
