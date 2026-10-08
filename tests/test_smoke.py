"""Same checks as scripts/check_env.py; skipped when the database is not reachable."""

from __future__ import annotations

import psycopg
import pytest

from etl.config import ConfigError, load_settings
from scripts.check_env import EnvironmentReport, inspect_environment


@pytest.fixture(scope="module")
def report() -> EnvironmentReport:
    try:
        settings = load_settings()
    except ConfigError as exc:
        pytest.skip(f"no database settings: {exc}")
    try:
        return inspect_environment(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"database not reachable at {settings.admin_db.safe_url}: {exc}")


def test_postgres_16(report: EnvironmentReport) -> None:
    assert report.postgres_version.startswith("PostgreSQL 16.")


def test_timescaledb_installed(report: EnvironmentReport) -> None:
    assert report.timescaledb_version is not None


def test_medallion_schemas_exist(report: EnvironmentReport) -> None:
    assert report.missing_schemas == ()


def test_roles_exist(report: EnvironmentReport) -> None:
    assert report.missing_roles == ()


def test_no_pending_migrations(report: EnvironmentReport) -> None:
    assert report.migrations_pending == ()
