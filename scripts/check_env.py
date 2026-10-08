"""Smoke test of the local environment.

Connects as the database owner (settings from .env) and checks that PostgreSQL is
reachable, TimescaleDB is installed, the schemas and roles exist, and no
migration is pending.

Usage, from the repo root with the virtual environment active:
    python scripts/check_env.py

Exit codes: 0 = OK, 1 = something is missing, 2 = configuration or connection error.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import psycopg

from etl.config import ConfigError, Settings, load_settings
from etl.migrations import CONNECT_TIMEOUT_SECONDS, MigrationError, migration_status

EXPECTED_SCHEMAS = ("bronze", "silver", "dwh", "dq", "meta")
EXPECTED_ROLES = ("bi_readonly", "grafana_reader", "powerbi_reader", "etl_writer")

# User schemas only: hides pg_catalog, information_schema and TimescaleDB internals.
_SCHEMAS_SQL = """
    SELECT nspname
    FROM pg_namespace
    WHERE nspname !~ '^(pg_|_timescaledb|timescaledb_|information_schema$)'
    ORDER BY nspname
"""


@dataclass(frozen=True)
class EnvironmentReport:
    postgres_version: str
    timescaledb_version: str | None
    schemas: tuple[str, ...]
    roles: tuple[str, ...]
    migrations_applied: int
    migrations_pending: tuple[str, ...]

    @property
    def missing_schemas(self) -> tuple[str, ...]:
        return tuple(s for s in EXPECTED_SCHEMAS if s not in self.schemas)

    @property
    def missing_roles(self) -> tuple[str, ...]:
        return tuple(r for r in EXPECTED_ROLES if r not in self.roles)

    @property
    def ok(self) -> bool:
        return (
            self.timescaledb_version is not None
            and not self.missing_schemas
            and not self.missing_roles
            and not self.migrations_pending
        )


def inspect_environment(settings: Settings) -> EnvironmentReport:
    """Query the database; raises psycopg.OperationalError if it is unreachable."""
    with psycopg.connect(settings.admin_db.url, connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
        postgres_version = conn.execute("SELECT version()").fetchone()[0]
        timescale_row = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
        ).fetchone()
        schemas = tuple(row[0] for row in conn.execute(_SCHEMAS_SQL))
        roles = tuple(
            row[0]
            for row in conn.execute(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s) ORDER BY rolname",
                (list(EXPECTED_ROLES),),
            )
        )
        applied, todo = migration_status(conn)
    return EnvironmentReport(
        postgres_version=postgres_version,
        timescaledb_version=timescale_row[0] if timescale_row else None,
        schemas=schemas,
        roles=roles,
        migrations_applied=len(applied),
        migrations_pending=tuple(m.label for m in todo),
    )


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    print("SolarBI environment check")
    print(f"  database    : {settings.admin_db.safe_url}")
    try:
        report = inspect_environment(settings)
    except psycopg.OperationalError as exc:
        print(f"CONNECTION ERROR: {exc}".rstrip(), file=sys.stderr)
        print("Is the stack running? Try: docker compose up -d", file=sys.stderr)
        return 2
    except MigrationError as exc:
        print(f"MIGRATION ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"  PostgreSQL  : {report.postgres_version}")
    print(f"  TimescaleDB : {report.timescaledb_version or 'NOT INSTALLED'}")
    print(f"  schemas     : {', '.join(report.schemas)}")
    print(f"  roles       : {', '.join(report.roles) or '-'}")
    print(
        f"  migrations  : {report.migrations_applied} applied, "
        f"{len(report.migrations_pending)} pending"
    )

    if report.ok:
        print("OK: TimescaleDB, schemas, roles and migrations are in place.")
        return 0
    if report.timescaledb_version is None:
        print("FAIL: the timescaledb extension is not installed.", file=sys.stderr)
    if report.missing_schemas:
        print(f"FAIL: missing schemas: {', '.join(report.missing_schemas)}", file=sys.stderr)
    if report.missing_roles:
        print(f"FAIL: missing roles: {', '.join(report.missing_roles)}", file=sys.stderr)
    if report.migrations_pending:
        print(
            f"FAIL: pending migrations: {', '.join(report.migrations_pending)} "
            "(run python scripts/migrate.py)",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
