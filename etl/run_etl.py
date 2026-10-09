"""Single entry point of the SolarBI ETL.

Usage, from the repository root with the virtual environment active:
    python -m etl.run_etl --file data/bronze/telemetria.csv
    python etl/run_etl.py --file data/bronze/telemetria.csv
    python -m etl.run_etl --file data/bronze/telemetria.csv --force-reload

Exit codes: 0 = run succeeded, 1 = run failed (recorded in dq.etl_run_log and
rolled back), 2 = configuration, contract or connection problem (no run opened).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

from etl.config import ConfigError, load_settings
from etl.contract import ContractError
from etl.pipeline import EtlRunError, RunReport, run_etl


def format_report(report: RunReport) -> str:
    """Step 2 report of the assignment (rows read, rejected per rule, valid, % valid)."""
    contract = report.contract
    by_action = {
        action: [r.id for r in contract.quality_rules if r.action == action]
        for action in ("reject", "dedupe", "flag")
    }

    def listing(action: str) -> str:
        return ", ".join(f"{rule}={report.rule_counts[rule]}" for rule in by_action[action])

    if report.bronze_status == "loaded":
        bronze = f"cargado ({report.bronze_rows_loaded} filas nuevas)"
    else:
        bronze = f"omitido: archivo ya cargado (filas de la corrida {report.bronze_run_id})"
    lines = [
        "",
        "Reporte de calidad (Paso 2)",
        f"Corrida                 : {report.run_id}",
        f"Archivo                 : {report.source.label} (contrato v{contract.version})",
        f"Bronze                  : {bronze}",
        f"Filas leídas            : {report.filas_leidas}",
        f"Rechazadas por regla    : {listing('reject')}",
        f"Rechazadas (distintas)  : {report.filas_rechazadas}",
        f"Deduplicadas            : {report.filas_deduplicadas}",
        f"Marcadas (flag)         : {listing('flag')}  ({report.filas_marcadas} filas)",
        f"Filas válidas           : {report.filas_validas}",
        f"% datos válidos         : {report.pct_validas:.2f} %",
        f"Días cargados en Gold   : {report.dias_gold}   "
        f"(energía total = {report.energia_kwh:.3f} kWh)",
        f"Eventos de falla        : {report.eventos_falla}",
        "",
        "Nota: los conteos por regla pueden solaparse (una fila puede fallar varias reglas).",
        f"Cuadre: leídas = válidas + rechazadas distintas + deduplicadas -> "
        f"{report.filas_leidas} = {report.filas_validas} + {report.filas_rechazadas} + "
        f"{report.filas_deduplicadas} ({'OK' if report.reconciles else 'NO CUADRA'})",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ETL SolarBI: Bronze -> Silver -> Gold.")
    parser.add_argument("--file", type=Path, required=True, help="CSV file to load")
    parser.add_argument("--contract", type=Path, default=None, help="contract YAML (optional)")
    parser.add_argument(
        "--force-reload",
        action="store_true",
        help="append the file to Bronze again even if the same checksum was already loaded",
    )
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"ERROR DE CONFIGURACIÓN: {exc}", file=sys.stderr)
        return 2
    try:
        report = run_etl(settings, args.file, args.contract, force_reload=args.force_reload)
    except (ContractError, FileNotFoundError) as exc:
        print(f"ERROR DE CONTRATO O ARCHIVO: {exc}", file=sys.stderr)
        return 2
    except EtlRunError as exc:
        print(f"LA CORRIDA FALLÓ y se revirtió: {exc}", file=sys.stderr)
        print("El detalle quedó en dq.etl_run_log (status = 'failed').", file=sys.stderr)
        return 1
    except psycopg.OperationalError as exc:
        print(f"ERROR DE CONEXIÓN: {exc}".rstrip(), file=sys.stderr)
        print("¿Está arriba la base? Pruebe: docker compose up -d", file=sys.stderr)
        return 2
    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
