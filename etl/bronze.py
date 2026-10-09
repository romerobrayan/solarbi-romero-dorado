"""Bronze: land a source file, as received, in bronze.telemetria_raw with COPY.

The file is streamed to PostgreSQL, which parses the CSV (the same path works for
a few hundred rows and for millions). Every value stays text; nothing is rejected
here. A file whose SHA-256 was already loaded by a successful run is not loaded
again (see etl/pipeline.py).
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import sql

from etl.config import PROJECT_ROOT
from etl.contract import Contract, ContractError
from etl.sqlgen import BRONZE_TABLE

CHUNK_BYTES = 1024 * 1024
STAGING = sql.Identifier("etl_crudo")
# Python codec name -> PostgreSQL client encoding for COPY.
PG_ENCODINGS = {
    "utf-8": "UTF8",
    "utf8": "UTF8",
    "latin-1": "LATIN1",
    "iso-8859-1": "LATIN1",
    "cp1252": "WIN1252",
}


@dataclass(frozen=True)
class SourceFile:
    path: Path
    label: str  # stored in source_file: path relative to the repo when inside it
    checksum: str  # SHA-256, hex
    size_bytes: int
    header: tuple[str, ...]


def inspect_source(path: Path, contract: Contract) -> SourceFile:
    """Checksum and header of the file; refuse it if the header breaks the contract."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"source file not found: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    encoding = contract.source.encoding.lower()
    header_encoding = "utf-8-sig" if encoding in ("utf-8", "utf8") else encoding  # drop a BOM
    with path.open(encoding=header_encoding, newline="") as f:
        header = tuple(next(csv.reader(f, delimiter=contract.source.delimiter), []))
    contract.check_source_header(header)
    return SourceFile(
        path=path,
        label=_label(path),
        checksum=digest.hexdigest(),
        size_bytes=path.stat().st_size,
        header=header,
    )


def find_loaded_run(conn: psycopg.Connection, checksum: str) -> UUID | None:
    """Run whose Bronze rows hold this file, if a successful run already loaded it."""
    row = conn.execute(
        """SELECT bronze_run_id FROM dq.etl_run_log
           WHERE source_checksum = %s AND status = 'succeeded' AND bronze_run_id IS NOT NULL
           ORDER BY started_at DESC LIMIT 1""",
        (checksum,),
    ).fetchone()
    return row[0] if row else None


def check_landing_table(conn: psycopg.Connection, contract: Contract) -> None:
    """The landing table must have one column per source column of the contract."""
    rows = conn.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = 'bronze' AND table_name = 'telemetria_raw'"""
    ).fetchall()
    existing = {name for (name,) in rows}
    missing = [name for name in contract.source_columns if name not in existing]
    if missing:
        raise ContractError(
            "bronze.telemetria_raw",
            [
                f"no landing column for source column {name!r}; add it with a migration"
                for name in missing
            ],
        )


def copy_to_bronze(
    conn: psycopg.Connection, source: SourceFile, contract: Contract, run_id: UUID
) -> int:
    """COPY the file into a temp table, then append it to Bronze. Returns rows loaded.

    Runs inside the caller's transaction, so a failed run leaves no Bronze rows.
    """
    header = [sql.Identifier(name) for name in source.header]
    encoding = PG_ENCODINGS.get(contract.source.encoding.lower(), contract.source.encoding)
    conn.execute(
        sql.SQL(
            "CREATE TEMP TABLE {} (source_row integer GENERATED ALWAYS AS IDENTITY, {}) "
            "ON COMMIT DROP"
        ).format(STAGING, sql.SQL(", ").join(sql.SQL("{} text").format(c) for c in header))
    )
    copy = sql.SQL(
        "COPY {} ({}) FROM STDIN "
        "(FORMAT csv, HEADER true, DELIMITER {}, ENCODING {}, FORCE_NOT_NULL ({}))"
    ).format(
        STAGING,
        sql.SQL(", ").join(header),
        sql.Literal(contract.source.delimiter),
        sql.Literal(encoding),
        sql.SQL(", ").join(header),
    )
    with conn.cursor() as cur, cur.copy(copy) as stream, source.path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_BYTES), b""):
            stream.write(chunk)

    landed = [sql.Identifier(name) for name in contract.source_columns]
    result = conn.execute(
        sql.SQL(
            """INSERT INTO {bronze} (run_id, source_file, source_row, {columns})
SELECT {run_id}, {label}, source_row, {columns}
FROM {staging}
ORDER BY source_row"""
        ).format(
            bronze=BRONZE_TABLE,
            columns=sql.SQL(", ").join(landed),
            run_id=sql.Literal(run_id),
            label=sql.Literal(source.label),
            staging=STAGING,
        )
    )
    return result.rowcount


def _label(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()
