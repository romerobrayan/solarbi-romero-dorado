"""Forward-only SQL migrations from sql/migrations/NNNN_description.sql.

- Applied in version order, each in its own transaction, and recorded in
  meta.schema_migrations (version, name, checksum, applied_at).
- Running again is a no-op. Editing a migration that was already applied is an
  error: changes always go in a new file.
- The runner connects as the database owner but executes each migration as the
  ETL role (SET LOCAL ROLE etl_writer), so etl_writer owns every table. A file
  that needs the owner (roles, grants) starts with the line "-- migrate:run-as owner".
- The runner creates its own bookkeeping (schema meta) before the first
  migration, like Flyway's history table, so any database can be migrated.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

from etl.config import ETL_ROLE, PROJECT_ROOT, DatabaseSettings, Settings

MIGRATIONS_DIR = PROJECT_ROOT / "sql" / "migrations"
CONNECT_TIMEOUT_SECONDS = 5
_ADVISORY_LOCK_ID = 20261008  # serializes concurrent runs of the migrator
_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")
_RUN_AS_OWNER = re.compile(r"^--\s*migrate:run-as\s+owner\s*$")

_BOOTSTRAP_SQL = """
CREATE SCHEMA IF NOT EXISTS meta;
COMMENT ON SCHEMA meta IS 'Pipeline bookkeeping: applied schema migrations.';
CREATE TABLE IF NOT EXISTS meta.schema_migrations (
    version      integer     PRIMARY KEY,
    name         text        NOT NULL,
    checksum     char(64)    NOT NULL,
    applied_at   timestamptz NOT NULL DEFAULT now(),
    applied_by   text        NOT NULL DEFAULT session_user,
    execution_ms integer     NOT NULL
);
"""


class MigrationError(RuntimeError):
    """A migration file is invalid, was edited after being applied, or failed."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str
    checksum: str
    run_as_owner: bool

    @property
    def label(self) -> str:
        return f"{self.version:04d}_{self.name}"


@dataclass(frozen=True)
class AppliedMigration:
    version: int
    name: str
    checksum: str


@dataclass(frozen=True)
class MigrationReport:
    applied_now: tuple[Migration, ...]
    current_version: int | None
    etl_login: str  # what happened to the etl_writer login


def checksum(sql_text: str) -> str:
    """SHA-256 of the file with normalized line endings (a CRLF checkout is not an edit)."""
    return hashlib.sha256(sql_text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Read every migration file, validating names and versions."""
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise MigrationError(
                f"{path.name}: migration files must be named NNNN_description.sql "
                "(four digits, lowercase letters, digits and underscores)"
            )
        text = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=int(match["version"]),
                name=match["name"],
                path=path,
                sql=text,
                checksum=checksum(text),
                run_as_owner=_declares_owner(text),
            )
        )
    repeated = [v for v, count in Counter(m.version for m in migrations).items() if count > 1]
    if repeated:
        raise MigrationError(f"duplicate migration versions: {sorted(repeated)}")
    return migrations


def pending(available: Sequence[Migration], applied: Sequence[AppliedMigration]) -> list[Migration]:
    """Migrations still to apply; raise if the applied history and the files disagree."""
    by_version = {migration.version: migration for migration in available}
    for record in applied:
        migration = by_version.get(record.version)
        if migration is None:
            raise MigrationError(
                f"migration {record.version:04d}_{record.name} is applied but its file is missing"
            )
        if migration.checksum != record.checksum:
            raise MigrationError(
                f"{migration.label} was modified after it was applied "
                f"(checksum {record.checksum[:12]}... is now {migration.checksum[:12]}...). "
                "Migrations are forward-only: revert the edit and add a new migration."
            )
    applied_versions = {record.version for record in applied}
    todo = [m for m in available if m.version not in applied_versions]
    if todo and applied_versions and todo[0].version < max(applied_versions):
        raise MigrationError(
            f"{todo[0].label} is older than the latest applied migration "
            f"({max(applied_versions):04d}); give it a higher number"
        )
    return todo


def migration_status(
    conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR
) -> tuple[list[AppliedMigration], list[Migration]]:
    """Applied and pending migrations, without changing anything."""
    has_history = conn.execute("SELECT to_regclass('meta.schema_migrations')").fetchone()[0]
    applied = _applied(conn) if has_history else []
    return applied, pending(discover(directory), applied)


def migrate(
    settings: Settings,
    directory: Path = MIGRATIONS_DIR,
    log: Callable[[str], None] = print,
) -> MigrationReport:
    """Apply every pending migration, then make sure etl_writer can log in."""
    available = discover(directory)
    with psycopg.connect(
        settings.admin_db.url, connect_timeout=CONNECT_TIMEOUT_SECONDS, autocommit=True
    ) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_ID,))
        try:
            with conn.transaction():
                conn.execute(_BOOTSTRAP_SQL)
            applied = _applied(conn)
            todo = pending(available, applied)
            for migration in todo:
                elapsed_ms = _apply(conn, migration)
                log(f"  applied {migration.label} ({elapsed_ms} ms)")
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_ID,))
        etl_login = _ensure_etl_login(conn, settings.db)

    versions = [record.version for record in applied] + [m.version for m in todo]
    return MigrationReport(
        applied_now=tuple(todo),
        current_version=max(versions) if versions else None,
        etl_login=etl_login,
    )


def _declares_owner(text: str) -> bool:
    """True if the leading comment block contains "-- migrate:run-as owner"."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("--"):
            return False
        if _RUN_AS_OWNER.match(stripped):
            return True
    return False


def _applied(conn: psycopg.Connection) -> list[AppliedMigration]:
    rows = conn.execute(
        "SELECT version, name, checksum FROM meta.schema_migrations ORDER BY version"
    ).fetchall()
    return [AppliedMigration(version=v, name=n, checksum=c) for v, n, c in rows]


def _apply(conn: psycopg.Connection, migration: Migration) -> int:
    started = time.perf_counter()
    try:
        with conn.transaction():
            if not migration.run_as_owner:
                conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(ETL_ROLE)))
            # No parameters: psycopg uses the simple query protocol, so a file
            # may contain several statements.
            conn.execute(migration.sql)
            conn.execute("RESET ROLE")
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            conn.execute(
                "INSERT INTO meta.schema_migrations (version, name, checksum, execution_ms) "
                "VALUES (%s, %s, %s, %s)",
                (migration.version, migration.name, migration.checksum, elapsed_ms),
            )
    except psycopg.Error as exc:
        raise MigrationError(f"{migration.label} failed and was rolled back: {exc}") from exc
    return elapsed_ms


def _ensure_etl_login(conn: psycopg.Connection, etl_db: DatabaseSettings) -> str:
    """Give etl_writer LOGIN and the password from .env when it cannot log in yet.

    SQL migrations cannot read environment variables, so the password is applied
    here. Checking first keeps repeated runs a no-op.
    """
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ETL_ROLE,)).fetchone()
    if exists is None:
        return f"{ETL_ROLE} does not exist yet"
    try:
        psycopg.connect(etl_db.url, connect_timeout=CONNECT_TIMEOUT_SECONDS).close()
    except psycopg.OperationalError:
        conn.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(ETL_ROLE), sql.Literal(etl_db.password)
            )
        )
        return f"{ETL_ROLE} login enabled with the password from ETL_WRITER_PASSWORD"
    return f"{ETL_ROLE} login OK"
