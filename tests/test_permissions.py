"""Permission model, against the running database (skipped if it is not reachable).

Proves the fix from migration 0001: a table created by etl_writer after the
migrations ran is readable by grafana_reader, which still cannot write and
cannot read bronze.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from conftest import unavailable
from psycopg import sql

from etl.config import ConfigError, DatabaseSettings, Settings, load_settings
from etl.migrations import CONNECT_TIMEOUT_SECONDS


def _connect(db: DatabaseSettings) -> psycopg.Connection:
    return psycopg.connect(db.url, connect_timeout=CONNECT_TIMEOUT_SECONDS, autocommit=True)


@pytest.fixture(scope="module")
def settings() -> Settings:
    try:
        settings = load_settings()
    except ConfigError as exc:
        unavailable(f"no database settings: {exc}")
    try:
        _connect(settings.db).close()
    except psycopg.OperationalError as exc:
        unavailable(f"cannot log in as etl_writer (stack down or migrations not applied): {exc}")
    return settings


@pytest.fixture(scope="module")
def grafana_reader(settings: Settings) -> DatabaseSettings:
    password = os.environ.get("GRAFANA_READER_PASSWORD")
    if not password:
        pytest.skip("GRAFANA_READER_PASSWORD is not set")
    return DatabaseSettings(
        host=settings.db.host,
        port=settings.db.port,
        name=settings.db.name,
        user="grafana_reader",
        password=password,
    )


@pytest.fixture
def probe_table(settings: Settings) -> Iterator[sql.Identifier]:
    """A dwh table created by etl_writer, like any table the ETL will create."""
    table = sql.Identifier("dwh", f"zz_probe_{uuid.uuid4().hex[:8]}")
    with _connect(settings.db) as conn:
        conn.execute(sql.SQL("CREATE TABLE {} (valor integer)").format(table))
        conn.execute(sql.SQL("INSERT INTO {} VALUES (42)").format(table))
    try:
        yield table
    finally:
        with _connect(settings.db) as conn:
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(table))


def test_grafana_reader_reads_tables_created_by_etl_writer(
    grafana_reader: DatabaseSettings, probe_table: sql.Identifier
) -> None:
    with _connect(grafana_reader) as conn:
        rows = conn.execute(sql.SQL("SELECT valor FROM {}").format(probe_table)).fetchall()
    assert rows == [(42,)]


def test_grafana_reader_cannot_write(
    grafana_reader: DatabaseSettings, probe_table: sql.Identifier
) -> None:
    with _connect(grafana_reader) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute(sql.SQL("INSERT INTO {} VALUES (1)").format(probe_table))
        # Even forcing a read-write transaction, the grants do not allow it.
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("SET TRANSACTION READ WRITE")
            conn.execute(sql.SQL("INSERT INTO {} VALUES (1)").format(probe_table))


def test_grafana_reader_cannot_read_bronze(grafana_reader: DatabaseSettings) -> None:
    with _connect(grafana_reader) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT count(*) FROM bronze.telemetria_raw")


def test_grafana_reader_reads_every_migrated_consumer_table(
    grafana_reader: DatabaseSettings,
) -> None:
    tables = [
        "silver.lectura_5min",
        "dwh.dim_fecha",
        "dwh.dim_sitio",
        "dwh.dim_dispositivo",
        "dwh.fact_energia_dia",
        "dq.etl_run_log",
        "dq.rule_result",
        "dq.fault_event",
        "dq.purga_log",
    ]
    with _connect(grafana_reader) as conn:
        for table in tables:
            conn.execute(
                sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(*table.split(".")))
            )


def test_etl_writer_owns_the_tables_and_is_not_superuser(settings: Settings) -> None:
    with _connect(settings.db) as conn:
        is_superuser = conn.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        ).fetchone()[0]
        owners = conn.execute(
            "SELECT DISTINCT tableowner FROM pg_tables "
            "WHERE schemaname IN ('bronze', 'silver', 'dwh', 'dq')"
        ).fetchall()
    assert is_superuser is False
    assert owners == [("etl_writer",)]
