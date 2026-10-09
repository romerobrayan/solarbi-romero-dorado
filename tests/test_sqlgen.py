"""Unit tests for the SQL generated from the contract (no database needed)."""

from __future__ import annotations

import re
import uuid
from dataclasses import replace

import pytest

from etl import sqlgen
from etl.config import PROJECT_ROOT
from etl.contract import Contract, load_contract

RUN = uuid.UUID("11111111-2222-3333-4444-555555555555")


@pytest.fixture(scope="module")
def contract() -> Contract:
    return load_contract(PROJECT_ROOT / "contracts" / "telemetria.yaml")


def _sql(composed: object) -> str:
    """Render without a connection; whitespace collapsed (psycopg pads negative numbers)."""
    return " ".join(composed.as_string(None).split())  # type: ignore[attr-defined]


def test_timestamp_layout_for_the_simulator_format() -> None:
    pattern, positions = sqlgen.timestamp_layout("%Y-%m-%d %H:%M:%S")
    assert re.fullmatch(pattern.strip("^$"), "2026-10-05 12:00:00")
    assert positions == {
        "%Y": (1, 4),
        "%m": (6, 2),
        "%d": (9, 2),
        "%H": (12, 2),
        "%M": (15, 2),
        "%S": (18, 2),
    }


def test_timestamp_layout_for_another_format_without_seconds() -> None:
    pattern, positions = sqlgen.timestamp_layout("%d/%m/%Y %H:%M")
    assert re.fullmatch(pattern.strip("^$"), "05/10/2026 12:00")
    assert positions == {"%d": (1, 2), "%m": (4, 2), "%Y": (7, 4), "%H": (12, 2), "%M": (15, 2)}


@pytest.mark.parametrize("fmt", ["%b %d %Y %H:%M", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S %S"])
def test_unsupported_timestamp_formats_are_refused(fmt: str) -> None:
    with pytest.raises(ValueError, match="timestamp_format"):
        sqlgen.timestamp_layout(fmt)


def test_source_identifiers_are_quoted(contract: Contract) -> None:
    """A hostile source column name ends up as a quoted identifier, never as SQL."""
    power = replace(contract.column("p_ac_kw"), source_name='p ac"kw; DROP TABLE x')
    hostile = replace(
        contract, columns=tuple(power if c.name == "p_ac_kw" else c for c in contract.columns)
    )
    text = _sql(sqlgen.stage_sql(hostile, RUN))
    assert 'b."p ac""kw; DROP TABLE x"' in text
    assert 'DROP TABLE x"' in text and "; DROP TABLE x " not in text


def test_reject_rules_decide_rejection_and_flag_rules_do_not(contract: Contract) -> None:
    text = _sql(sqlgen.stage_sql(contract, RUN))
    rejected = re.search(r"SELECT checked\.\*, \((.*?)\) AS \"rechazada\"", text, re.S).group(1)
    for rule in contract.quality_rules:
        if rule.action == "reject":
            assert f'"rule__{rule.id}"' in rejected
        else:
            assert f'"rule__{rule.id}"' not in rejected


def test_flag_rules_are_written_to_dq_flags(contract: Contract) -> None:
    text = _sql(sqlgen.silver_upsert_sql(contract, RUN))
    flags = [r.id for r in contract.quality_rules if r.action == "flag"]
    for rule_id in flags:
        assert f"CASE WHEN r.\"rule__{rule_id}\" THEN '{rule_id}' END" in text
    assert 'ON CONFLICT ("dispositivo_id", "ts") DO UPDATE SET' in text
    assert f"'{RUN.hex}'::uuid" in text  # psycopg renders UUID literals as plain hex


def test_dedupe_keeps_first_by_source_row_on_the_snapped_key(contract: Contract) -> None:
    text = _sql(sqlgen.stage_sql(contract, RUN))
    partition = 'PARTITION BY "rechazada", "dispositivo_id", "ts_slot"'
    assert f"{partition} ORDER BY decided.source_row ASC" in text
    keep_last = replace(contract, dedup_keep="last")
    assert "ORDER BY decided.source_row DESC" in _sql(sqlgen.stage_sql(keep_last, RUN))


def test_ranges_come_from_the_contract(contract: Contract) -> None:
    text = _sql(sqlgen.rule_failure(contract, _rule(contract, "range_p_ac_kw")))
    assert text == 'COALESCE(("p_ac_kw" < 0.0 OR "p_ac_kw" > 1.1 * "dev__nominal_kwp"), false)'
    temp = _sql(sqlgen.rule_failure(contract, _rule(contract, "range_temp_modulo")))
    assert temp == 'COALESCE(("temp_modulo_c" < -10.0 OR "temp_modulo_c" > 90.0), false)'


def test_grid_rules_use_the_contract_tolerance(contract: Contract) -> None:
    off_grid = _sql(sqlgen.rule_failure(contract, _rule(contract, "off_grid")))
    assert off_grid == 'COALESCE(abs(extract(epoch FROM "ts" - "ts_slot")) > 60, false)'
    snapped = _sql(sqlgen.rule_failure(contract, _rule(contract, "snapped_to_grid")))
    assert snapped == 'COALESCE("ts" <> "ts_slot", false)'


def test_fault_rule_threshold_operator_and_window(contract: Contract) -> None:
    rule = next(r for r in contract.fault_rules if r.id == "zero_power_daylight")
    text = _sql(sqlgen.fault_rule_sql(contract, rule))
    assert 'l."p_ac_kw" <= 0.01' in text
    assert "(l.ts AT TIME ZONE dd.zona)::time >= '09:00:00'::time" in text
    assert "HAVING count(*) >= 1" in text
    assert "irradiancia_wm2" not in text  # the irradiance gate is disabled in the contract


def test_gold_uses_contract_frequency_expected_readings_and_sla(contract: Contract) -> None:
    text = _sql(sqlgen.gold_upsert_sql(contract, RUN))
    assert "round((a.suma_potencia * 300 / 3600.0)::numeric, 4)" in text
    assert "round(100.0 * a.lecturas / 288, 2) >= 95.0" in text
    assert "ARRAY['missing_irradiancia', 'range_irradiancia']::text[]" in text
    assert "ON CONFLICT (fecha_key, dispositivo_key) DO UPDATE SET" in text


def test_generated_sql_has_no_placeholders(contract: Contract) -> None:
    """Values are literals, so no statement depends on %-placeholder parsing."""
    statements = [
        sqlgen.stage_sql(contract, RUN),
        sqlgen.counts_sql(contract),
        sqlgen.silver_upsert_sql(contract, RUN),
        sqlgen.gold_upsert_sql(contract, RUN),
        *sqlgen.dimension_sql(contract),
        *sqlgen.faults_sql(contract, RUN),
    ]
    for statement in statements:
        assert "%" not in _sql(statement)


def _rule(contract: Contract, rule_id: str):  # noqa: ANN202
    return next(r for r in contract.quality_rules if r.id == rule_id)
