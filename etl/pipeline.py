"""ETL orchestration: contract -> Bronze -> Silver -> dimensions -> Gold -> faults.

Python only orchestrates; the transformations are set-based SQL generated from
the contract (etl/sqlgen.py) and run inside PostgreSQL as etl_writer.

Transactions:
- the run is opened in dq.etl_run_log first, in its own transaction, so even a
  failed run leaves a trace;
- everything else (Bronze, Silver, dimensions, Gold, faults, per-rule results and
  the 'succeeded' status) commits in ONE transaction. A failure rolls all of it
  back, then the run is closed as 'failed' with the error message.

Idempotency: a file already loaded by a successful run (same SHA-256) is not
appended to Bronze again; its Bronze rows are transformed again and written with
UPSERT, so a second run really executes and still leaves the same counts.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import sql

from etl import sqlgen
from etl.bronze import SourceFile, check_landing_table, copy_to_bronze, find_loaded_run
from etl.bronze import inspect_source as _inspect_source
from etl.config import Settings
from etl.contract import Contract, ContractError, load_contract

CONNECT_TIMEOUT_SECONDS = 10
ETL_LOCK = "solarbi_etl"  # advisory lock: one run at a time
STEPS = 8


class EtlRunError(RuntimeError):
    """A run failed after it was opened; it is recorded as 'failed' in dq.etl_run_log."""

    def __init__(self, run_id: uuid.UUID, cause: BaseException) -> None:
        self.run_id = run_id
        self.cause = cause
        super().__init__(f"run {run_id} failed: {cause}")


@dataclass
class RunReport:
    run_id: uuid.UUID
    contract: Contract
    source: SourceFile
    bronze_status: str = ""
    bronze_run_id: uuid.UUID | None = None
    bronze_rows_loaded: int = 0
    filas_leidas: int = 0
    filas_rechazadas: int = 0
    filas_deduplicadas: int = 0
    filas_validas: int = 0
    filas_marcadas: int = 0
    rule_counts: dict[str, int] = field(default_factory=dict)
    dias_gold: int = 0
    energia_kwh: Decimal = Decimal(0)
    eventos_falla: int = 0
    timings: list[tuple[str, float]] = field(default_factory=list)

    @property
    def pct_validas(self) -> float:
        return 100.0 * self.filas_validas / self.filas_leidas if self.filas_leidas else 0.0

    @property
    def reconciles(self) -> bool:
        return self.filas_leidas == (
            self.filas_validas + self.filas_rechazadas + self.filas_deduplicadas
        )


def run_etl(
    settings: Settings,
    source_path: Path,
    contract_path: Path | None = None,
    force_reload: bool = False,
    log: Callable[[str], None] = print,
) -> RunReport:
    """Run the whole pipeline for one file. Raises ContractError before opening a run,
    EtlRunError if the run fails after being opened."""
    step = _StepTimer(log)

    with step(1, "Contrato y archivo"):
        contract = load_contract(contract_path or settings.paths.contracts_dir / "telemetria.yaml")
        source = _inspect_source(source_path, contract)
    log(f"      contrato {contract.dataset} v{contract.version}; archivo {source.label}")
    log(f"      sha256 {source.checksum}")

    run_id = uuid.uuid4()
    report = RunReport(run_id=run_id, contract=contract, source=source)
    with psycopg.connect(
        settings.db.url, connect_timeout=CONNECT_TIMEOUT_SECONDS, autocommit=True
    ) as conn:
        check_landing_table(conn, contract)
        with step(2, "Abrir corrida en dq.etl_run_log"):
            conn.execute(
                """INSERT INTO dq.etl_run_log
                       (run_id, contract_version, source_file, source_checksum, status)
                   VALUES (%s, %s, %s, %s, 'running')""",
                (run_id, contract.version, source.label, source.checksum),
            )
        log(f"      run_id {run_id}")
        try:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (ETL_LOCK,))
                _load(conn, contract, source, report, step, force_reload)
                with step(8, "Cerrar corrida (succeeded)"):
                    _close_succeeded(conn, report)
        except Exception as exc:
            _close_failed(conn, run_id, exc)
            raise EtlRunError(run_id, exc) from exc
    report.timings = step.timings
    return report


def _load(
    conn: psycopg.Connection,
    contract: Contract,
    source: SourceFile,
    report: RunReport,
    step: _StepTimer,
    force_reload: bool,
) -> None:
    run_id = report.run_id

    with step(3, "Bronze"):
        previous = None if force_reload else find_loaded_run(conn, source.checksum)
        if previous is None:
            report.bronze_rows_loaded = copy_to_bronze(conn, source, contract, run_id)
            report.bronze_status, report.bronze_run_id = "loaded", run_id
        else:
            report.bronze_status, report.bronze_run_id = "skipped_duplicate_file", previous
    if report.bronze_status == "loaded":
        log_line = f"      {report.bronze_rows_loaded} filas copiadas a bronze.telemetria_raw"
    else:
        log_line = (
            "      archivo ya cargado (mismo sha256): se reutilizan sus filas de Bronze "
            f"de la corrida {report.bronze_run_id}"
        )
    step.log(log_line)

    with step(4, "Silver (reglas de calidad + UPSERT)"):
        conn.execute(sqlgen.stage_sql(contract, report.bronze_run_id))
        cursor = conn.execute(sqlgen.counts_sql(contract))
        values = dict(zip([c.name for c in cursor.description], cursor.fetchone(), strict=True))
        report.filas_leidas = values["leidas"]
        report.filas_rechazadas = values["rechazadas"]
        report.filas_deduplicadas = values["deduplicadas"]
        report.filas_validas = values["validas"]
        report.filas_marcadas = values["marcadas"]
        report.rule_counts = {r.id: values[f"rule__{r.id}"] for r in contract.quality_rules}
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO dq.rule_result (run_id, rule_id, action, filas_afectadas)
                   VALUES (%s, %s, %s, %s)""",
                [
                    (run_id, rule.id, rule.action, report.rule_counts[rule.id])
                    for rule in contract.quality_rules
                ],
            )
        conn.execute(sqlgen.silver_upsert_sql(contract, run_id))

    with step(5, "Dimensiones"):
        for statement in sqlgen.dimension_sql(contract):
            conn.execute(statement)
        conn.execute(sqlgen.days_sql())
        conn.execute(sqlgen.ensure_dates_sql())

    with step(6, "Gold (dwh.fact_energia_dia por día local)"):
        report.dias_gold, report.energia_kwh = conn.execute(
            sqlgen.gold_upsert_sql(contract, run_id)
        ).fetchone()

    with step(7, "Fallas (dq.fault_event)"):
        statements = sqlgen.faults_sql(contract, run_id)
        for statement in statements:
            conn.execute(statement)
        if statements:
            report.eventos_falla = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sqlgen.FAULTS)
            ).fetchone()[0]


def _close_succeeded(conn: psycopg.Connection, report: RunReport) -> None:
    conn.execute(
        """UPDATE dq.etl_run_log SET
               status = 'succeeded', finished_at = clock_timestamp(),
               bronze_status = %s, bronze_run_id = %s,
               filas_leidas = %s, filas_validas = %s, filas_rechazadas = %s,
               filas_deduplicadas = %s, filas_marcadas = %s,
               dias_gold = %s, eventos_falla = %s
           WHERE run_id = %s""",
        (
            report.bronze_status,
            report.bronze_run_id,
            report.filas_leidas,
            report.filas_validas,
            report.filas_rechazadas,
            report.filas_deduplicadas,
            report.filas_marcadas,
            report.dias_gold,
            report.eventos_falla,
            report.run_id,
        ),
    )


def _close_failed(conn: psycopg.Connection, run_id: uuid.UUID, exc: BaseException) -> None:
    message = f"{type(exc).__name__}: {exc}"[:2000]
    conn.execute(
        """UPDATE dq.etl_run_log
           SET status = 'failed', finished_at = clock_timestamp(), error_message = %s
           WHERE run_id = %s""",
        (message, run_id),
    )


class _StepTimer:
    """Context manager factory that times and logs the numbered steps of a run."""

    def __init__(self, log: Callable[[str], None]) -> None:
        self.log = log
        self.timings: list[tuple[str, float]] = []

    def __call__(self, number: int, name: str) -> _Step:
        return _Step(self, number, name)


class _Step:
    def __init__(self, timer: _StepTimer, number: int, name: str) -> None:
        self.timer, self.number, self.name = timer, number, name

    def __enter__(self) -> None:
        self.started = time.perf_counter()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        elapsed = time.perf_counter() - self.started
        self.timer.timings.append((self.name, elapsed))
        status = "ok" if exc_type is None else "ERROR"
        self.timer.log(f"[{self.number}/{STEPS}] {self.name:<44} {status:>5}  {elapsed:6.2f} s")


__all__ = ["ContractError", "EtlRunError", "RunReport", "run_etl"]
