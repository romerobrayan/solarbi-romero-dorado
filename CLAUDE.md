# CLAUDE.md — SolarBI Pascual

Context for AI-assisted sessions on this repository. Read it fully before changing anything.

## Purpose

University data-engineering project (Inteligencia de Negocios, Grupo 01, Institución Universitaria
Pascual Bravo, 2026-II, professor Ramiro Grisales Montoya). Solo author: Brayan Romero Dorado.
SolarBI Pascual monitors a solar plant whose inverters send telemetry every 5 minutes. Group 01
focus: **operational monitoring — power over time and fault detection**.

"One governed dataset, two views: Grafana for real-time operations and Power BI for business decisions."

## Roadmap (current phase marked)

| Phase | Name | Status |
|---|---|---|
| 0 | Foundations: repo, local infra, conventions | done |
| 1 | Architecture & data contract | done |
| 2 | ETL (Bronze -> Silver -> Gold) with quality rules | done |
| 3 | Dashboards (Grafana + Power BI) | done (pending Brayan's Power BI steps in Desktop + screenshots) |
| 4 | Research (Part A, written outside this repo) | in progress |
| 5 | Delivery (PDF + repo) | **next** |
| 6 | Scale to the real 4M+ row dataset (unknown schema) | pending |
| 7 | Final dashboard / star schema | pending |

Each phase arrives as a separate prompt. Do not start a phase that was not requested.

## Architecture

Medallion, single PostgreSQL 16 + TimescaleDB database (`docker-compose.yml`, service `db`):

- **Contract**: `contracts/telemetria.yaml` (v1.1.0, validated by `contracts/telemetria.schema.json`
  + `etl/contract.py`) defines source format, time zone, columns, ranges, quality rules
  (`reject | flag | dedupe`), the 5-minute grid (`grid`, ±60 s), fault rules, devices/sites, SLA
  and a changelog. Load it with `etl.contract.load_contract(path)`.
- **Bronze**: raw CSV in `data/bronze/` (`telemetria.csv` from `etl/simulador.py`), landed as text
  in `bronze.telemetria_raw` with `COPY`. Append-only (a trigger rejects UPDATE/DELETE/TRUNCATE).
  A day purge (`scripts/purge_days.py`) never deletes Bronze: its `dq.purga_log` row marks the
  older Bronze rows of those days superseded and `stage_sql` skips them (ADR 0007 update).
- **Silver**: `silver.lectura_5min` hypertable (7-day chunks), `ts` in UTC snapped to the grid
  (original in `ts_origen`), unique `(dispositivo_id, ts)`, `dq_flags text[]` holds ids of failed
  `flag` rules.
- **Gold**: `dwh.fact_energia_dia`, PK `(fecha_key, dispositivo_key)`, grain = the site's LOCAL day
  (never the UTC day), idempotent UPSERT (`INSERT ... ON CONFLICT ... DO UPDATE`). Dimensions
  `dim_fecha` (filled 2020-2035, `dwh.ensure_dim_fecha()`), `dim_sitio`, `dim_dispositivo`
  (seeded from the contract).
- **DQ**: `dq.etl_run_log` (per-run `pct_validas`, distinct totals that reconcile:
  leidas = validas + rechazadas + deduplicadas), `dq.rule_result`, `dq.fault_event`, `dq.purga_log`.
- **ETL (ELT, ADR 0007)**: `etl/run_etl.py` (CLI) -> `etl/pipeline.py` (one transaction per run)
  -> `etl/bronze.py` (COPY, skip by SHA-256) and `etl/sqlgen.py` (all SQL generated from the
  contract with `psycopg.sql`; never format SQL strings; run ids and values as `sql.Literal`).
- **Roles**: `etl_writer` (non-superuser, owns all tables, used by the ETL), owner (migrations only),
  `grafana_reader` / `powerbi_reader` read-only through `bi_readonly` (`silver`, `dwh`, `dq`;
  never `bronze`).

- **Grafana** (ADR 0008): dashboard `grafana/dashboards/solarbi-operacion.json` (provisioned,
  UI edits allowed, then `scripts/export_grafana.py` writes it back normalized; default range
  `now-7d`, never future data). Alerting in `grafana/provisioning/alerting/`: two rules on Silver,
  `Potencia cero en horario solar` (critical, latest reading at zero, `noDataState: OK`) and
  `Inversor sin datos en horario solar` (warning, catalog-driven: `dim_dispositivo` LEFT JOIN the
  last 15 min); thresholds/windows must match the contract (tests compare them). Webhook contact
  point to the `alert-receiver` service (compose profile `alerting`) for critical, email contact
  point (default route, production) for the rest. Rule files are NOT
  env-interpolated by Grafana: write `$__timeFilter` / `{{ $labels.x }}` literally (never `$$`);
  contact-point files are (`${VAR}`).
- **Power BI** (ADR 0008): Import mode, `powerbi_reader`, Gold only. Brayan saves
  `powerbi/SolarBI.pbip` from Desktop (`docs/powerbi.md`); `scripts/powerbi_model.py --apply` adds
  relationships, date table and the `_Medidas` DAX in TMDL (Desktop must be closed).

Diagrams: `docs/architecture.md`. Decisions: `docs/adr/` (template `0000-template.md`).

## Language rules

- **Spanish**: README, commit messages, user-facing docs (`docs/architecture.md`, reports).
- **English**: code identifiers, code comments, ADRs, this file.
- **Exception**: domain tables and columns keep the professor's Spanish names:
  `fact_energia_dia`, `lectura_5min`, `energia_kwh`, `pct_datos_validos`, `p_ac_kw`,
  `irradiancia_wm2`, `temp_modulo_c`, `dispositivo_id`, `ts`.

## Naming conventions

- Schemas: `bronze`, `silver`, `dwh`, `dq`. Facts `dwh.fact_<name>`, dimensions `dwh.dim_<name>`.
- Silver tables: `<entity>_<grain>` (e.g. `lectura_5min`).
- Keys: surrogate keys `<dim>_key` in `dwh`; natural identifiers `<entity>_id`.
- Columns: snake_case, unit as suffix (`_kw`, `_kwh`, `_wm2`, `_c`), percentages prefixed `pct_`.
- Timestamps: column `ts`, type `timestamptz`, stored in UTC.
- SQL files: `sql/init/NN_name.sql|sh` run only on the first start of an empty volume
  (bootstrap only: extension, schemas, reader roles). Everything else is a migration
  `sql/migrations/NNNN_description.sql`.
- ADRs: `docs/adr/NNNN-kebab-case-title.md`.
- Commits: Conventional Commits in Spanish (`feat(etl): ...`, `fix(sql): ...`, `docs: ...`).
  Never "update" or "cambios". No `Co-Authored-By` or other AI attribution trailers (owner's
  decision for this public repo).

## Commands (PowerShell, repo root)

```powershell
Copy-Item .env.example .env            # first time only, then edit the passwords
docker compose up -d                   # start db + grafana
docker compose ps                      # both must be healthy
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python scripts/migrate.py              # apply pending migrations (no-op if none); --status to list
python scripts/check_env.py            # DB, TimescaleDB, schemas, roles, no pending migrations
python etl/simulador.py                # professor's simulator -> data/bronze/telemetria.csv
python etl/simulador_fallas.py --seed 42 --inject-faults   # -> data/samples/
python -m etl.run_etl --file data/bronze/telemetria.csv     # the ETL (re-run = idempotent)
python scripts/conteos.py              # row counts per layer + energy per day
docker compose --profile alerting up -d                     # + webhook receiver for alerts
python scripts/replay_live.py --trip-in 5m --trip-minutes 20   # live zero-power alert, run 09:00-15:00 local
python scripts/replay_live.py --gap-in 5m --gap-minutes 20     # live silent-inverter alert, same hours
python scripts/purge_days.py --from D --to D --motivo "..."    # dry run; --apply purges (Bronze is marked)
python scripts/export_grafana.py       # dashboard JSON from Grafana into the repo (--check compares)
python scripts/powerbi_model.py --apply   # TMDL relationships + measures, after the .pbip is saved
pytest                                 # DB tests skip if the stack is down (fail if SOLARBI_REQUIRE_DB=1)
ruff check .
docker compose down                    # stop; add -v to wipe volumes and re-run sql/init
```

Settings come from `etl/config.py` (`load_settings()`), which reads `.env` and env vars:
`settings.db` is the ETL connection (`etl_writer`), `settings.admin_db` the owner (migrations and
checks only).

Integration tests (`tests/test_etl_integration.py`) run against a throwaway database
`<POSTGRES_DB>_test` created and dropped by `tests/conftest.py`; never point tests at the dev data.
CI (`.github/workflows/ci.yml`) bootstraps a fresh TimescaleDB service with `sql/init/*`, migrates,
and runs ruff + pytest on Python 3.12 and 3.14 with `SOLARBI_REQUIRE_DB=1`.

`etl/simulador.py` is the professor's code, kept verbatim: never reformat or "fix" it (ruff ignores it).

## Migrations workflow

1. Never edit an applied migration (checksum error); never renumber one. Add a new file with the
   next number: `sql/migrations/NNNN_description.sql` (lowercase, underscores).
2. Files run as `etl_writer` (so it owns what they create). Only a file that needs the owner
   (roles, grants, extensions) starts with `-- migrate:run-as owner`.
3. Each file is one transaction: no `CREATE INDEX CONCURRENTLY`, no continuous aggregate
   `WITH DATA` (create it `WITH NO DATA` and refresh from the ETL).
4. Run `python scripts/migrate.py`, then `pytest` (permissions and smoke tests check the result).

## Hard rules

1. Never commit `.env`, credentials, connection strings or real data. Only the small simulator
   outputs may be committed (`data/bronze/telemetria.csv`, `data/silver/*.csv`); the 4M-row
   dataset lives in an ignored folder. `tests/test_repo_hygiene.py` enforces a 5 MB limit.
2. Bronze is immutable: read it, never rewrite or "fix" it in place.
3. Every load must be idempotent: re-running the ETL on the same input yields the same tables.
4. Only `contracts/` knows source column names, units, ranges and thresholds (ADR 0002). Code uses
   the canonical Silver names and reads every rule and threshold from the contract.
5. Grafana and Power BI connect with read-only roles, never the owner. The ETL connects as
   `etl_writer`, never the owner.
6. Store `ts` in UTC; daily grain is the site's local day (ADR 0004).
7. Pin image tags and Python dependencies to exact versions; never `latest`.
8. Ask before pushing, creating a GitHub repository or any other remote resource.
9. Do not modify the sibling folder `../consulta/` (written research and phase prompts; read-only).
10. Docs and commands must work in PowerShell on Windows; no bash-only entry points.
