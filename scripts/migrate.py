"""Apply pending SQL migrations (sql/migrations/) to the database.

Usage, from the repo root with the virtual environment active:
    python scripts/migrate.py            # apply what is pending (no-op if nothing is)
    python scripts/migrate.py --status   # list applied and pending, change nothing

Exit codes: 0 = OK, 1 = migration error, 2 = configuration or connection error.
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from etl.config import ConfigError, load_settings
from etl.migrations import (
    CONNECT_TIMEOUT_SECONDS,
    MigrationError,
    migrate,
    migration_status,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply SolarBI SQL migrations.")
    parser.add_argument(
        "--status", action="store_true", help="show applied and pending migrations only"
    )
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Database: {settings.admin_db.safe_url}")
    try:
        if args.status:
            return _print_status(settings.admin_db.url)
        report = migrate(settings)
    except psycopg.OperationalError as exc:
        print(f"CONNECTION ERROR: {exc}".rstrip(), file=sys.stderr)
        print("Is the stack running? Try: docker compose up -d", file=sys.stderr)
        return 2
    except MigrationError as exc:
        print(f"MIGRATION ERROR: {exc}", file=sys.stderr)
        return 1

    version = "none" if report.current_version is None else f"{report.current_version:04d}"
    if report.applied_now:
        print(f"Applied {len(report.applied_now)} migration(s); database is at version {version}.")
    else:
        print(f"Nothing to migrate: database is already at version {version}.")
    print(report.etl_login)
    return 0


def _print_status(url: str) -> int:
    with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
        applied, todo = migration_status(conn)
    for record in applied:
        print(f"  applied  {record.version:04d}_{record.name}")
    for migration in todo:
        print(f"  pending  {migration.label}")
    print(f"{len(applied)} applied, {len(todo)} pending.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
