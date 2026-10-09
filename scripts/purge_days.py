"""Purge whole local days from Silver, Gold and fault events (one-off maintenance).

Used when data that should not be there was loaded (e.g. a simulated sample dated in
the future). Bronze is append-only, so its rows are NOT deleted: the purge's row in
dq.purga_log marks the Bronze rows of those days loaded before the purge as
superseded, and the ETL skips them, so re-running an old file cannot bring the days
back. Rows loaded after the purge (a new file, --force-reload, the live replay) count
again.

Runs as etl_writer, in one transaction, under the ETL's advisory lock (never at the
same time as a run). Without --apply it only reports what it would remove and rolls
back.

Usage, from the repo root:
    python scripts/purge_days.py --from 2026-10-08 --to 2026-10-10 --motivo "..."
    python scripts/purge_days.py --from 2026-10-08 --to 2026-10-10 --motivo "..." --apply
"""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

from etl import sqlgen
from etl.config import ConfigError, Settings, load_settings
from etl.contract import Contract, ContractError, load_contract
from etl.pipeline import CONNECT_TIMEOUT_SECONDS, ETL_LOCK


@dataclass(frozen=True)
class PurgeReport:
    purga_id: uuid.UUID
    dia_desde: date
    dia_hasta: date
    zona_horaria: str
    ts_desde: datetime
    ts_hasta: datetime
    filas_bronze_reemplazadas: int
    filas_silver: int
    filas_gold: int
    eventos_falla: int
    applied: bool


def site_timezone(contract: Contract) -> ZoneInfo:
    """The days are local days of the site; one time zone is needed to cut them."""
    zones = {site.timezone.key for site in contract.sites}
    if len(zones) != 1:
        raise ValueError(f"the sites use {len(zones)} time zones; purge one site at a time")
    return ZoneInfo(zones.pop())


def purge_days(
    settings: Settings,
    desde: date,
    hasta: date,
    motivo: str,
    apply: bool = False,
    contract: Contract | None = None,
) -> PurgeReport:
    if hasta < desde:
        raise ValueError(f"--to {hasta} is before --from {desde}")
    if not motivo.strip():
        raise ValueError("--motivo must explain why the days are purged")
    contract = contract or load_contract(settings.paths.contracts_dir / "telemetria.yaml")
    tz = site_timezone(contract)
    ts_desde = datetime.combine(desde, time(0), tz)
    ts_hasta = datetime.combine(hasta + timedelta(days=1), time(0), tz)
    purga_id = uuid.uuid4()

    with (
        psycopg.connect(
            settings.db.url, connect_timeout=CONNECT_TIMEOUT_SECONDS, autocommit=True
        ) as conn,
        conn.transaction(force_rollback=not apply),
    ):
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (ETL_LOCK,))
        bronze = conn.execute(
            sqlgen.superseded_bronze_sql(contract, ts_desde, ts_hasta)
        ).fetchone()[0]
        silver = conn.execute(
            "DELETE FROM silver.lectura_5min WHERE ts >= %s AND ts < %s", (ts_desde, ts_hasta)
        ).rowcount
        gold = conn.execute(
            "DELETE FROM dwh.fact_energia_dia WHERE fecha_key BETWEEN %s AND %s",
            (int(f"{desde:%Y%m%d}"), int(f"{hasta:%Y%m%d}")),
        ).rowcount
        faults = conn.execute(
            "DELETE FROM dq.fault_event WHERE ts_inicio >= %s AND ts_inicio < %s",
            (ts_desde, ts_hasta),
        ).rowcount
        conn.execute(
            """INSERT INTO dq.purga_log
                   (purga_id, dia_desde, dia_hasta, zona_horaria, ts_desde, ts_hasta, motivo,
                    filas_bronze_reemplazadas, filas_silver, filas_gold, eventos_falla)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                purga_id,
                desde,
                hasta,
                tz.key,
                ts_desde,
                ts_hasta,
                motivo.strip(),
                bronze,
                silver,
                gold,
                faults,
            ),
        )
    return PurgeReport(
        purga_id=purga_id,
        dia_desde=desde,
        dia_hasta=hasta,
        zona_horaria=tz.key,
        ts_desde=ts_desde,
        ts_hasta=ts_hasta,
        filas_bronze_reemplazadas=bronze,
        filas_silver=silver,
        filas_gold=gold,
        eventos_falla=faults,
        applied=apply,
    )


def format_report(report: PurgeReport) -> str:
    utc = "%Y-%m-%d %H:%M UTC"
    lines = [
        f"Purga de días {report.dia_desde} a {report.dia_hasta} ({report.zona_horaria})",
        f"  rango             {report.ts_desde.astimezone(ZoneInfo('UTC')):{utc}}"
        f" -> {report.ts_hasta.astimezone(ZoneInfo('UTC')):{utc}} (fin excluido)",
        f"  bronze.telemetria_raw {report.filas_bronze_reemplazadas:>6} filas marcadas como "
        "reemplazadas (no se borran: Bronze es de solo inserción)",
        f"  silver.lectura_5min   {report.filas_silver:>6} filas borradas",
        f"  dwh.fact_energia_dia  {report.filas_gold:>6} filas borradas",
        f"  dq.fault_event        {report.eventos_falla:>6} eventos borrados",
    ]
    if report.applied:
        lines.append(f"Aplicada y registrada en dq.purga_log (purga_id {report.purga_id}).")
    else:
        lines.append("SIMULACIÓN: no se borró nada (se revirtió). Agregue --apply para ejecutarla.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Purge local days from Silver, Gold and faults.")
    parser.add_argument("--from", dest="desde", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="hasta", type=date.fromisoformat, required=True)
    parser.add_argument("--motivo", required=True, help="why the days are purged (logged)")
    parser.add_argument("--apply", action="store_true", help="execute (default: dry run)")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        report = purge_days(settings, args.desde, args.hasta, args.motivo, apply=args.apply)
    except (ConfigError, ContractError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
