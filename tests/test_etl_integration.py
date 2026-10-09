"""End-to-end ETL tests against a throwaway database (see conftest.test_settings).

Each scenario uses its own dates in 2030, so tests do not interfere with each other.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from etl import sqlgen
from etl.config import Settings
from etl.contract import ContractError
from etl.pipeline import EtlRunError, RunReport, run_etl
from etl.simulador_fallas import SimulationConfig, generate_rows, write_csv

FIXTURES = Path(__file__).parent / "fixtures"
ANOMALIES = FIXTURES / "telemetria_anomalias.csv"
TABLES = (
    "bronze.telemetria_raw",
    "silver.lectura_5min",
    "dwh.fact_energia_dia",
    "dq.fault_event",
)


def _quiet(_message: str) -> None:
    pass


def _run(settings: Settings, path: Path, **kwargs: object) -> RunReport:
    return run_etl(settings, path, log=_quiet, **kwargs)


def _counts(settings: Settings) -> dict[str, int]:
    with psycopg.connect(settings.db.url) as conn:
        return {
            table: conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(*table.split(".")))
            ).fetchone()[0]
            for table in TABLES
        }


def _simulated(tmp_path: Path, start: date, seed: int, faults: bool = False, days: int = 2) -> Path:
    path = tmp_path / f"sim_{start:%Y%m%d}_{seed}.csv"
    config = SimulationConfig(seed=seed, days=days, start=start, inject_faults=faults)
    write_csv(path, generate_rows(config))
    return path


@pytest.fixture(scope="module")
def anomalies(test_settings: Settings) -> RunReport:
    return _run(test_settings, ANOMALIES)


# --- quality rules on the known-anomaly fixture ------------------------------------


def test_exact_counts_per_rule(anomalies: RunReport) -> None:
    assert anomalies.rule_counts == {
        "missing_key": 1,
        "invalid_format": 2,
        "unknown_device": 1,
        "off_grid": 1,
        "snapped_to_grid": 1,
        "range_p_ac_kw": 2,
        "missing_p_ac_kw": 1,
        "missing_irradiancia": 1,
        "duplicate_key": 1,
        "range_irradiancia": 1,
        "range_temp_modulo": 1,
    }


def test_report_reconciles(anomalies: RunReport) -> None:
    assert (anomalies.filas_leidas, anomalies.filas_validas) == (18, 9)
    assert (anomalies.filas_rechazadas, anomalies.filas_deduplicadas) == (8, 1)
    assert anomalies.filas_marcadas == 4
    assert anomalies.reconciles
    assert anomalies.pct_validas == pytest.approx(50.0)


def test_run_log_and_rule_results_are_recorded(
    test_settings: Settings, anomalies: RunReport
) -> None:
    with psycopg.connect(test_settings.db.url) as conn:
        status, leidas, validas, pct, contract_version = conn.execute(
            """SELECT status, filas_leidas, filas_validas, pct_validas, contract_version
               FROM dq.etl_run_log WHERE run_id = %s""",
            (anomalies.run_id,),
        ).fetchone()
        rules = dict(
            conn.execute(
                "SELECT rule_id, filas_afectadas FROM dq.rule_result WHERE run_id = %s",
                (anomalies.run_id,),
            ).fetchall()
        )
    assert (status, leidas, validas, contract_version) == ("succeeded", 18, 9, "1.1.0")
    assert pct == Decimal("50.00")
    assert rules == anomalies.rule_counts


def test_snapped_reading_keeps_its_original_timestamp(
    test_settings: Settings, anomalies: RunReport
) -> None:
    with psycopg.connect(test_settings.db.url) as conn:
        row = conn.execute(
            """SELECT ts, ts_origen, dq_flags FROM silver.lectura_5min
               WHERE dispositivo_id = '1' AND ts_origen IS NOT NULL
                 AND ts::date = DATE '2030-01-15'"""
        ).fetchall()
    # 10:35:30 local (UTC-5) is snapped to 10:35 local = 15:35 UTC.
    assert row == [
        (
            datetime(2030, 1, 15, 15, 35, tzinfo=UTC),
            datetime(2030, 1, 15, 15, 35, 30, tzinfo=UTC),
            ["snapped_to_grid"],
        )
    ]


def test_off_grid_reading_is_not_in_silver(test_settings: Settings, anomalies: RunReport) -> None:
    with psycopg.connect(test_settings.db.url) as conn:
        found = conn.execute(
            """SELECT count(*) FROM silver.lectura_5min
               WHERE ts BETWEEN '2030-01-15 15:40+00' AND '2030-01-15 15:45+00'"""
        ).fetchone()[0]
    assert found == 0


def test_daily_grain_is_the_local_day(test_settings: Settings, anomalies: RunReport) -> None:
    """23:55 on Jan 15 in Bogota is 04:55 UTC on Jan 16; it must count for Jan 15."""
    with psycopg.connect(test_settings.db.url) as conn:
        rows = conn.execute(
            """SELECT fecha_key, energia_kwh, lecturas_validas, lecturas_esperadas,
                      pct_datos_validos, cumple_sla, p_max_kw, irradiacion_kwh_m2
               FROM dwh.fact_energia_dia WHERE fecha_key IN (20300115, 20300116)
               ORDER BY fecha_key"""
        ).fetchall()
    assert rows == [
        # 8 readings: sum p = 19.8 kW -> 19.8 x 5/60 = 1.65 kWh;
        # irradiance without flags: 3160 W/m2 x 5/60 / 1000 = 0.2633 kWh/m2
        (
            20300115,
            Decimal("1.6500"),
            8,
            288,
            Decimal("2.78"),
            False,
            Decimal("3.300"),
            Decimal("0.2633"),
        ),
        (
            20300116,
            Decimal("0.0000"),
            1,
            288,
            Decimal("0.35"),
            False,
            Decimal("0.000"),
            Decimal("0.0000"),
        ),
    ]


def test_missing_daytime_readings_become_events(
    test_settings: Settings, anomalies: RunReport
) -> None:
    with psycopg.connect(test_settings.db.url) as conn:
        events = conn.execute(
            """SELECT rule_id, (ts_inicio AT TIME ZONE 'America/Bogota')::time, lecturas
               FROM dq.fault_event
               WHERE ts_inicio >= '2030-01-15' AND ts_inicio < '2030-01-17'
               ORDER BY ts_inicio"""
        ).fetchall()
    # Gaps of at least 3 slots inside 09:00-15:00 local; the 2-slot gap at 10:10 is ignored.
    # Jan 16 has no event: its only reading is 00:00, so nothing later can be "missing" yet
    # (the same rule keeps a live feed from flagging the rest of today as missing).
    assert [(rule, start.isoformat(), n) for rule, start, n in events] == [
        ("missing_daytime_reading", "09:00:00", 12),
        ("missing_daytime_reading", "10:40:00", 4),
        ("missing_daytime_reading", "11:05:00", 47),
    ]
    assert anomalies.eventos_falla == 3


# --- fault injection ----------------------------------------------------------------


def test_inverter_trip_and_gap_are_detected(test_settings: Settings, tmp_path: Path) -> None:
    path = _simulated(tmp_path, date(2030, 2, 1), seed=42, faults=True, days=3)
    report = _run(test_settings, path)
    with psycopg.connect(test_settings.db.url) as conn:
        events = conn.execute(
            """SELECT rule_id, severity, ts_inicio, ts_fin, lecturas FROM dq.fault_event
               WHERE ts_inicio >= '2030-02-01' AND ts_inicio < '2030-02-04'
               ORDER BY ts_inicio"""
        ).fetchall()
    trips = [e for e in events if e[0] == "zero_power_daylight"]
    # Second day, 12:00-12:40 local = 17:00-17:40 UTC, 8 readings at zero power.
    assert trips == [
        (
            "zero_power_daylight",
            "critical",
            datetime(2030, 2, 2, 17, 0, tzinfo=UTC),
            datetime(2030, 2, 2, 17, 40, tzinfo=UTC),
            8,
        )
    ]
    gaps = [e for e in events if e[0] == "missing_daytime_reading"]
    gap_start, gap_end = (
        datetime(2030, 2, 3, 15, 0, tzinfo=UTC),
        datetime(2030, 2, 3, 15, 20, tzinfo=UTC),
    )
    assert any(e[2] <= gap_start and e[3] >= gap_end and e[4] >= 4 for e in gaps)
    assert report.eventos_falla == len(events)


# --- idempotency ----------------------------------------------------------------


def test_second_run_changes_nothing(test_settings: Settings, tmp_path: Path) -> None:
    path = _simulated(tmp_path, date(2030, 3, 1), seed=7, faults=True)
    first = _run(test_settings, path)
    counts_after_first = _counts(test_settings)
    energy_first = _energy(test_settings, "2030-03-01", "2030-03-03")

    second = _run(test_settings, path)

    assert first.bronze_status == "loaded"
    assert second.bronze_status == "skipped_duplicate_file"
    assert second.bronze_run_id == first.run_id
    assert _counts(test_settings) == counts_after_first
    assert _energy(test_settings, "2030-03-01", "2030-03-03") == energy_first
    for field in (
        "filas_leidas",
        "filas_validas",
        "filas_rechazadas",
        "filas_deduplicadas",
        "rule_counts",
        "dias_gold",
        "energia_kwh",
        "eventos_falla",
    ):
        assert getattr(second, field) == getattr(first, field), field
    # The second run really executed: every Silver row of those days now carries its run_id.
    with psycopg.connect(test_settings.db.url) as conn:
        run_ids = conn.execute(
            """SELECT DISTINCT run_id FROM silver.lectura_5min
               WHERE ts >= '2030-03-01 05:00+00' AND ts < '2030-03-03 05:00+00'"""
        ).fetchall()
    assert run_ids == [(second.run_id,)]


def test_force_reload_appends_bronze_but_keeps_silver_and_gold(
    test_settings: Settings, tmp_path: Path
) -> None:
    path = _simulated(tmp_path, date(2030, 4, 1), seed=11)
    first = _run(test_settings, path)
    before = _counts(test_settings)

    again = _run(test_settings, path, force_reload=True)

    after = _counts(test_settings)
    assert again.bronze_status == "loaded"
    assert after["bronze.telemetria_raw"] == before["bronze.telemetria_raw"] + first.filas_leidas
    assert {t: after[t] for t in TABLES[1:]} == {t: before[t] for t in TABLES[1:]}


# --- failures ----------------------------------------------------------------


def test_failed_run_rolls_back_and_is_logged(
    test_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _simulated(tmp_path, date(2030, 5, 1), seed=13)
    before = _counts(test_settings)
    monkeypatch.setattr(
        sqlgen, "gold_upsert_sql", lambda contract, run_id: sql.SQL("SELECT 1/0, 0")
    )

    with pytest.raises(EtlRunError) as excinfo:
        _run(test_settings, path)

    assert _counts(test_settings) == before  # Bronze and Silver were rolled back too
    with psycopg.connect(test_settings.db.url) as conn:
        status, error = conn.execute(
            "SELECT status, error_message FROM dq.etl_run_log WHERE run_id = %s",
            (excinfo.value.run_id,),
        ).fetchone()
    assert status == "failed"
    assert "division by zero" in error


def test_file_breaking_the_contract_is_refused_before_any_run(
    test_settings: Settings, tmp_path: Path
) -> None:
    path = tmp_path / "renamed.csv"
    path.write_text(
        "ts,dispositivo_id,potencia_kw,irradiancia_wm2,temp_modulo_c\n"
        "2030-06-01 10:00:00,1,2.5,600,40\n",
        encoding="utf-8",
    )
    runs_before = _run_count(test_settings)
    with pytest.raises(ContractError, match="'p_ac_kw' is missing"):
        _run(test_settings, path)
    assert _run_count(test_settings) == runs_before


# --- consumers ----------------------------------------------------------------


def test_grafana_reader_sees_the_loaded_layers(
    test_settings: Settings, anomalies: RunReport
) -> None:
    password = os.environ.get("GRAFANA_READER_PASSWORD")
    if not password:
        pytest.skip("GRAFANA_READER_PASSWORD is not set")
    reader = replace(test_settings.db, user="grafana_reader", password=password)
    with psycopg.connect(reader.url) as conn:
        for table in (
            "silver.lectura_5min",
            "dwh.fact_energia_dia",
            "dq.fault_event",
            "dq.etl_run_log",
            "dq.rule_result",
            "dwh.dim_dispositivo",
        ):
            count = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(*table.split(".")))
            ).fetchone()[0]
            assert count > 0, table
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM bronze.telemetria_raw LIMIT 1")


# --- helpers ----------------------------------------------------------------


def _energy(settings: Settings, first_day: str, last_day: str) -> list[tuple[int, Decimal]]:
    with psycopg.connect(settings.db.url) as conn:
        return conn.execute(
            """SELECT fecha_key, energia_kwh FROM dwh.fact_energia_dia
               WHERE fecha_key BETWEEN to_char(%s::date, 'YYYYMMDD')::int
                                   AND to_char(%s::date, 'YYYYMMDD')::int
               ORDER BY fecha_key""",
            (first_day, last_day),
        ).fetchall()


def _run_count(settings: Settings) -> int:
    with psycopg.connect(settings.db.url) as conn:
        return conn.execute("SELECT count(*) FROM dq.etl_run_log").fetchone()[0]
