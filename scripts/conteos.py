"""Row counts per layer and energy per day: run before and after an ETL run to
check that a repeated run leaves the data unchanged (idempotency).

Usage, from the repo root with the virtual environment active:
    python scripts/conteos.py
"""

from __future__ import annotations

import sys

import psycopg

from etl.config import ConfigError, load_settings

COUNTS_SQL = """
SELECT 'bronze.telemetria_raw' AS tabla, count(*) AS filas FROM bronze.telemetria_raw
UNION ALL SELECT 'silver.lectura_5min', count(*) FROM silver.lectura_5min
UNION ALL SELECT 'dwh.fact_energia_dia', count(*) FROM dwh.fact_energia_dia
UNION ALL SELECT 'dq.fault_event', count(*) FROM dq.fault_event
UNION ALL SELECT 'dq.etl_run_log', count(*) FROM dq.etl_run_log
"""

ENERGY_SQL = """
SELECT f.fecha_key, d.dispositivo_id, f.energia_kwh, f.lecturas_validas, f.pct_datos_validos
FROM dwh.fact_energia_dia AS f
JOIN dwh.dim_dispositivo AS d USING (dispositivo_key)
ORDER BY f.fecha_key, d.dispositivo_id
"""


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"ERROR DE CONFIGURACIÓN: {exc}", file=sys.stderr)
        return 2
    with psycopg.connect(settings.db.url, connect_timeout=10) as conn:
        print(f"{'tabla':<24} {'filas':>8}")
        for tabla, filas in conn.execute(COUNTS_SQL):
            print(f"{tabla:<24} {filas:>8}")
        print()
        print(
            f"{'fecha_key':<10} {'disp.':<6} {'energia_kwh':>12} {'lecturas':>9} {'% válidos':>10}"
        )
        for fecha_key, dispositivo, energia, lecturas, pct in conn.execute(ENERGY_SQL):
            print(f"{fecha_key:<10} {dispositivo:<6} {energia:>12} {lecturas:>9} {pct:>10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
