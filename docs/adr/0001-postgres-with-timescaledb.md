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
