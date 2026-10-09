"""Shared fixtures.

Database tests skip when the database is unreachable, except when SOLARBI_REQUIRE_DB=1
(set in CI), where they fail instead: in CI a skipped DB test would hide a broken setup.

ETL tests run against a throwaway database "<POSTGRES_DB>_test" in the same cluster,
bootstrapped with sql/init/00_schemas.sql and migrated like a fresh volume, so they
never write into the development data (Bronze is append-only).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace

import psycopg
import pytest
from psycopg import sql

from etl.config import PROJECT_ROOT, ConfigError, Settings, load_settings
from etl.migrations import migrate

REQUIRE_DB = os.environ.get("SOLARBI_REQUIRE_DB") == "1"
INIT_SQL = PROJECT_ROOT / "sql" / "init" / "00_schemas.sql"


def unavailable(reason: str) -> None:
    """Skip locally; fail when the environment requires the database (CI)."""
    if REQUIRE_DB:
        pytest.fail(f"database required (SOLARBI_REQUIRE_DB=1): {reason}", pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def settings() -> Settings:
    try:
        return load_settings()
    except ConfigError as exc:
        unavailable(f"no database settings: {exc}")
        raise  # unreachable; keeps type checkers happy


@pytest.fixture(scope="session")
def test_settings(settings: Settings) -> Iterator[Settings]:
    """Settings pointing at a fresh, migrated test database (dropped afterwards)."""
    name = f"{settings.admin_db.name}_test"
    try:
        admin = psycopg.connect(settings.admin_db.url, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as exc:
        unavailable(f"database not reachable at {settings.admin_db.safe_url}: {exc}")
        raise
    database = sql.Identifier(name)
    with admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(database))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(database))
    test = replace(
        settings, db=replace(settings.db, name=name), admin_db=replace(settings.admin_db, name=name)
    )
    with psycopg.connect(test.admin_db.url, autocommit=True) as conn:
        conn.execute(INIT_SQL.read_text(encoding="utf-8"))
    migrate(test, log=lambda _message: None)
    yield test
    with psycopg.connect(settings.admin_db.url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(database))
