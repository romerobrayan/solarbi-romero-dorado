# 0006. Forward-only SQL migrations, applied as a dedicated non-superuser ETL role

- **Status:** Accepted
- **Date:** 2026-10-08
- **Phase:** 1

## Context

`sql/init/` runs only when the data volume is empty. Every table added after the first start
would otherwise need `docker compose down -v`, which deletes all data. That is acceptable with
870 simulated rows and unacceptable with the 4M-row dataset.

In Phase 0 the ETL would have connected as the database owner, which is a superuser in the
official image. A bug in the ETL could then drop roles, alter other databases or bypass every
permission. Also, `ALTER DEFAULT PRIVILEGES` in `00_schemas.sql` only applies to objects created
by the role that ran it (the owner): tables created by any other role would be invisible to Grafana
and Power BI.

Options considered: Alembic (Python, model-driven; heavy for a SQL-first project and hides the DDL
the course grades), Flyway (Java runtime), or a small runner of numbered `.sql` files.

## Decision

- **Migrations** are plain SQL files `sql/migrations/NNNN_description.sql`, applied in order by
  `python scripts/migrate.py` (logic in `etl/migrations.py`). They are **forward-only**: no down
  migrations; a mistake is fixed by a new migration.
- Each file runs in **its own transaction** and is recorded in `meta.schema_migrations`
  (`version`, `name`, `checksum`, `applied_at`, `applied_by`, `execution_ms`).
  Re-running is a no-op. An applied file whose SHA-256 changed is an error, as are a missing applied
  file and a new file numbered below the latest applied one. Line endings are normalized before
  hashing, so a CRLF checkout on Windows is not an edit.
- The runner **creates `meta` itself** (like Flyway's history table) instead of `sql/init/`, so a
  volume created before Phase 1 is migrated without being recreated.
- An advisory lock serializes concurrent runs.
- New role **`etl_writer`**: `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE`, `USAGE, CREATE` on
  `bronze, silver, dwh, dq, meta`. `etl/config.py` uses it for the ETL (`Settings.db`); the owner
  is only used for migrations and checks (`Settings.admin_db`).
- **Who runs migrations:** the runner connects as the owner but executes each file under
  `SET LOCAL ROLE etl_writer`, so **etl_writer owns every table** (and can load, truncate staging
  tables and manage hypertable chunks without superuser rights). A file that genuinely needs the
  owner declares `-- migrate:run-as owner` in its header; today only `0001_etl_writer_role.sql`
  does, because it creates the role and grants on the schemas.
- **etl_writer is created by migration 0001, not by the init script**, so old and new volumes
  follow exactly the same path. SQL files cannot read environment variables, so the role is created
  `NOLOGIN` and `migrate.py` then enables `LOGIN` with `ETL_WRITER_PASSWORD` from `.env`, only if
  logging in with it fails (rotating the password in `.env` and re-running `migrate.py` applies it).
- The default-privileges bug is fixed in 0001:
  `ALTER DEFAULT PRIVILEGES FOR ROLE etl_writer IN SCHEMA silver, dwh, dq GRANT SELECT ON TABLES TO bi_readonly;`
  `tests/test_permissions.py` proves that `grafana_reader` reads a table created by `etl_writer`,
  cannot write, and cannot read `bronze`.

## Consequences

- No reset is ever needed to evolve the schema; `docker compose down -v` stays a deliberate
  "start from scratch", never a step in the workflow.
- The DDL the course grades is readable as plain SQL in the repo, in the order it was applied.
- Statements that cannot run inside a transaction (`CREATE INDEX CONCURRENTLY`, a continuous
  aggregate created `WITH DATA`) are not supported in a migration. In Phase 6, create continuous
  aggregates `WITH NO DATA` in a migration and refresh them from the ETL.
- Forward-only means rollback is a new migration. Acceptable for one author and a single database.
- Bronze is append-only at the database level (a trigger rejects UPDATE, DELETE and TRUNCATE on
  `bronze.telemetria_raw`). Removing a bad load needs the owner to disable the trigger on purpose.
