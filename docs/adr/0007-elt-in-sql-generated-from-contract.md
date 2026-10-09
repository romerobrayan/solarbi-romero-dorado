# 0007. ELT in SQL generated from the contract, orchestrated by Python

- **Status:** Accepted
- **Date:** 2026-10-08
- **Phase:** 2

## Context

The assignment allows the quality rules to be applied "with Python (pandas) or with SQL". The
pipeline must run with one command, be idempotent, report counts per rule, and keep working when
the 4M+ row dataset arrives (Phase 6) with a schema we do not know yet. ADR 0002 already put every
column name, range and threshold in `contracts/telemetria.yaml`.

Options considered:

- **ETL in pandas**: read the CSV into a DataFrame, clean it in Python, insert the result. Simple
  for 870 rows; for 4M rows it needs the whole file in memory (the development machine has under
  2 GB free), moves every row through Python twice, and the quality logic lives outside the
  database where Grafana and the audit tables are.
- **ELT in SQL**: land the raw file in the database with `COPY`, then transform with set-based SQL
  inside PostgreSQL. The database does the parsing, typing, deduplication and aggregation it is
  built for.

## Decision

**ELT in SQL, generated from the contract, orchestrated by Python** (`etl/run_etl.py` →
`etl/pipeline.py`):

1. **Extract/Load**: the file is streamed with `COPY ... FROM STDIN (FORMAT csv)` into a temporary
   table with an identity column (`source_row`), then appended to `bronze.telemetria_raw` with the
   run id. Every value stays text; nothing is rejected in Bronze.
2. **Transform**: `etl/sqlgen.py` writes the SQL from the contract: parsing per declared type
   (timestamps via the contract's format and time zone), the 5-minute grid, one boolean per quality
   rule, the reject/flag/dedupe decision, the Silver UPSERT, the Gold aggregation per local day and
   the fault-event detection (gaps-and-islands). Python never touches individual rows.
3. **Injection safety**: identifiers always go through `psycopg.sql.Identifier` and contract
   values (thresholds, time zones, device ids, the run id) through `psycopg.sql.Literal`. There is
   no string formatting of SQL, and statements run without `%`-placeholders, so a value containing
   `%` or a quote cannot break or inject a query. `tests/test_sqlgen.py` covers a hostile source
   column name.
4. **One transaction per run** for Bronze, Silver, dimensions, Gold, faults and the per-rule
   results; the run is opened beforehand and closed as `failed` (with the error) if anything breaks.
5. **No pandas** in the pipeline; it was removed from the dependencies.

Performance notes learned while building it:

- The intermediate CTEs are `MATERIALIZED`. Inlined, each reference to a parsed column re-ran its
  parse expression for every rule that read it (15 s for 879 rows).
- Timestamps are validated with a boolean regex (`~`) and fields cut with `substr()` at fixed
  positions; `regexp_match` with capture groups was ~20× slower in PostgreSQL.
- Result: the whole run takes about 0.3 s for 879 rows (Silver ~0.07–0.3 s), i.e. a linear estimate
  of a few minutes for 4M rows.

## Consequences

- The same code path serves 870 and 4M rows; memory use in Python is constant (the file is streamed).
- The answer to the report's ETL vs. ELT question is concrete: Bronze is the "L" before the "T".
- Plugging in a new dataset is a contract change (plus a landing-table migration if its columns
  differ), not new Python.
- Debugging needs SQL literacy: the generated SQL can be printed (`sqlgen.stage_sql(contract,
  run_id).as_string(conn)`) and is quoted in `docs/evidencias/fase-2-ejecucion.md`.
- Every rule kind (`parseable`, `not_null`, `range`, `known_device`, `grid`, `on_grid`, `unique`)
  needs one SQL template; a new kind of check is a code change, a new rule of an existing kind is not.
- Timestamp formats are limited to fixed-width strftime tokens (`%Y %m %d %H %M %S`); anything else
  is refused when the SQL is generated, with a clear message.
