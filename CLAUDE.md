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
| 1 | Architecture & data contract | **next** |
| 2 | ETL (Bronze -> Silver -> Gold) with quality rules | pending |
| 3 | Dashboards (Grafana + Power BI) | pending |
| 4 | Research | pending |
| 5 | Delivery | pending |
| 6 | Scale to the real 4M+ row dataset (unknown schema) | pending |
| 7 | Final dashboard / star schema | pending |

Each phase arrives as a separate prompt. Do not start a phase that was not requested.

## Architecture

Medallion, single PostgreSQL 16 + TimescaleDB database (`docker-compose.yml`, service `db`):

- **Bronze**: raw CSV in `data/bronze/` (`telemetria.csv` from `etl/simulador.py`). Immutable.
- **Silver**: `silver.lectura_5min`, one row per `(dispositivo_id, ts)`, cleaned and validated
  against the data contract.
- **Gold**: `dwh.fact_energia_dia`, PK `(fecha_key, dispositivo_key)`, loaded with an idempotent
  UPSERT (`INSERT ... ON CONFLICT ... DO UPDATE`).
- **DQ**: `dq.etl_run_log` and rule results; every ETL run is logged.
- **Consumers**: Grafana (service `grafana`, role `grafana_reader`) and Power BI Desktop
  (role `powerbi_reader`). Both are read-only through the group role `bi_readonly`
  (`silver`, `dwh`, `dq`; never `bronze`).

Diagram: `docs/architecture.md`. Decisions: `docs/adr/` (template `0000-template.md`).

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
- SQL files: `sql/init/NN_name.sql|sh` run only on the first start of an empty volume.
- ADRs: `docs/adr/NNNN-kebab-case-title.md`.
- Commits: Conventional Commits in Spanish (`feat(etl): ...`, `fix(sql): ...`, `docs: ...`).
  Never "update" or "cambios".

## Commands (PowerShell, repo root)

```powershell
Copy-Item .env.example .env            # first time only, then edit the passwords
docker compose up -d                   # start db + grafana
docker compose ps                      # both must be healthy
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python scripts/check_env.py            # DB, TimescaleDB, schemas, roles
pytest                                 # DB tests skip if the stack is down
ruff check .
docker compose down                    # stop; add -v to wipe volumes and re-run sql/init
```

Settings come from `etl/config.py` (`load_settings()`), which reads `.env` and env vars.

## Hard rules

1. Never commit `.env`, credentials, connection strings or real data. Only the small simulator
   outputs may be committed (`data/bronze/telemetria.csv`, `data/silver/*.csv`); the 4M-row
   dataset lives in an ignored folder. `tests/test_repo_hygiene.py` enforces a 5 MB limit.
2. Bronze is immutable: read it, never rewrite or "fix" it in place.
3. Every load must be idempotent: re-running the ETL on the same input yields the same tables.
4. Never hardcode dataset column names outside `contracts/` (see ADR 0002). Code uses the
   canonical Silver names only.
5. Grafana and Power BI connect with read-only roles, never the owner.
6. Pin image tags and Python dependencies to exact versions; never `latest`.
7. Ask before pushing, creating a GitHub repository or any other remote resource.
8. Do not touch the sibling folder `../consulta/` (written research).
9. Docs and commands must work in PowerShell on Windows; no bash-only entry points.
