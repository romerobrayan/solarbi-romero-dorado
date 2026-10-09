"""Tests for etl/contract.py: the real contract loads; broken contracts fail clearly."""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from etl.config import PROJECT_ROOT
from etl.contract import ContractError, load_contract

CONTRACT_PATH = PROJECT_ROOT / "contracts" / "telemetria.yaml"
SOURCE_HEADER = ["ts", "dispositivo_id", "p_ac_kw", "irradiancia_wm2", "temp_modulo_c"]


@pytest.fixture(scope="module")
def contract_data() -> dict[str, Any]:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _broken(
    contract_data: dict[str, Any], tmp_path: Path, mutate: Callable[[dict[str, Any]], None]
) -> ContractError:
    data = copy.deepcopy(contract_data)
    mutate(data)
    with pytest.raises(ContractError) as excinfo:
        load_contract(_write(tmp_path, data))
    return excinfo.value


def _column(data: dict[str, Any], name: str) -> dict[str, Any]:
    return next(column for column in data["columns"] if column["name"] == name)


def _rule(data: dict[str, Any], rule_id: str) -> dict[str, Any]:
    return next(rule for rule in data["quality_rules"] if rule["id"] == rule_id)


# --- the real contract ----------------------------------------------------------


def test_real_contract_loads_with_expected_values() -> None:
    contract = load_contract(CONTRACT_PATH)

    assert contract.version == "1.1.0"
    assert contract.major_version == 1
    assert contract.timezone.key == "America/Bogota"
    assert contract.frequency_seconds == 300
    assert contract.expected_readings_per_day == 288
    assert contract.interval_hours == pytest.approx(5 / 60)
    assert contract.natural_key == ("dispositivo_id", "ts")
    assert contract.source_columns == tuple(SOURCE_HEADER)
    assert (contract.grid.column, contract.grid.tolerance_seconds) == ("ts", 60)
    assert [entry.version for entry in contract.changelog] == ["1.1.0", "1.0.0"]


def test_real_contract_rule_actions() -> None:
    contract = load_contract(CONTRACT_PATH)

    actions = {rule.id: rule.action for rule in contract.quality_rules}
    assert actions == {
        "missing_key": "reject",
        "invalid_format": "reject",
        "unknown_device": "reject",
        "off_grid": "reject",
        "snapped_to_grid": "flag",
        "range_p_ac_kw": "reject",
        "missing_p_ac_kw": "reject",
        "missing_irradiancia": "flag",
        "duplicate_key": "dedupe",
        "range_irradiancia": "flag",
        "range_temp_modulo": "flag",
    }


def test_power_maximum_is_relative_to_device_nominal_power() -> None:
    contract = load_contract(CONTRACT_PATH)

    assert contract.max_value("p_ac_kw", "1") == pytest.approx(5.5)
    assert contract.max_value("irradiancia_wm2") == 1500
    with pytest.raises(ValueError, match="depends on the device"):
        contract.max_value("p_ac_kw")


def test_zero_power_fault_window_is_local_and_half_open() -> None:
    rule = next(
        r for r in load_contract(CONTRACT_PATH).fault_rules if r.id == "zero_power_daylight"
    )

    assert (rule.operator, rule.threshold) == ("<=", 0.01)
    assert rule.window.contains(time(9, 0))
    assert rule.window.contains(time(14, 55))
    assert not rule.window.contains(time(15, 0))
    assert rule.irradiance_gate is not None and not rule.irradiance_gate.enabled


# --- broken contracts -------------------------------------------------------------


def test_missing_required_column_fails(contract_data: dict[str, Any], tmp_path: Path) -> None:
    def drop_power(data: dict[str, Any]) -> None:
        data["columns"] = [c for c in data["columns"] if c["name"] != "p_ac_kw"]

    error = _broken(contract_data, tmp_path, drop_power)
    assert "columns: required column 'p_ac_kw' is missing" in error.problems
    # the rules that referenced it are reported too
    assert any("range_p_ac_kw" in p and "'p_ac_kw' is not declared" in p for p in error.problems)


def test_unknown_action_fails(contract_data: dict[str, Any], tmp_path: Path) -> None:
    def unknown_action(data: dict[str, Any]) -> None:
        _rule(data, "missing_irradiancia")["action"] = "drop"

    index = [r["id"] for r in contract_data["quality_rules"]].index("missing_irradiancia")
    error = _broken(contract_data, tmp_path, unknown_action)
    assert any(
        p.startswith(f"quality_rules[{index}].action:") and "'drop' is not one of" in p
        for p in error.problems
    )


def test_inverted_range_fails(contract_data: dict[str, Any], tmp_path: Path) -> None:
    def inverted(data: dict[str, Any]) -> None:
        _column(data, "temp_modulo_c")["range"] = {"min": 90, "max": -10}

    error = _broken(contract_data, tmp_path, inverted)
    assert any(
        "temp_modulo_c" in p and "min (90) must be lower than max (-10)" in p
        for p in error.problems
    )


def test_range_with_two_maximums_fails(contract_data: dict[str, Any], tmp_path: Path) -> None:
    def two_maximums(data: dict[str, Any]) -> None:
        _column(data, "p_ac_kw")["range"]["max"] = 6.0

    error = _broken(contract_data, tmp_path, two_maximums)
    assert any("both max and max_nominal_factor" in p for p in error.problems)


def test_unsupported_major_version_fails(contract_data: dict[str, Any], tmp_path: Path) -> None:
    def bump_major(data: dict[str, Any]) -> None:
        data["contract_version"] = "2.0.0"

    error = _broken(contract_data, tmp_path, bump_major)
    assert any("major version 2 is not supported" in p for p in error.problems)


def test_unknown_timezone_fails(contract_data: dict[str, Any], tmp_path: Path) -> None:
    def bad_timezone(data: dict[str, Any]) -> None:
        data["source"]["source_timezone"] = "Colombia/Medellin"

    error = _broken(contract_data, tmp_path, bad_timezone)
    assert any("unknown time zone 'Colombia/Medellin'" in p for p in error.problems)


def test_frequency_must_divide_a_day(contract_data: dict[str, Any], tmp_path: Path) -> None:
    def odd_frequency(data: dict[str, Any]) -> None:
        data["source"]["frequency_seconds"] = 7

    error = _broken(contract_data, tmp_path, odd_frequency)
    assert any("does not divide a day" in p for p in error.problems)


def test_nullable_natural_key_fails(contract_data: dict[str, Any], tmp_path: Path) -> None:
    def nullable_key(data: dict[str, Any]) -> None:
        _column(data, "ts")["nullable"] = True

    error = _broken(contract_data, tmp_path, nullable_key)
    assert "keys.natural_key: column 'ts' must not be nullable" in error.problems


def test_device_with_unknown_site_fails(contract_data: dict[str, Any], tmp_path: Path) -> None:
    def orphan_device(data: dict[str, Any]) -> None:
        data["devices"][0]["sitio_id"] = "XX-99"

    error = _broken(contract_data, tmp_path, orphan_device)
    assert any("sitio_id 'XX-99' is not declared in sites" in p for p in error.problems)


def test_grid_tolerance_must_be_below_half_the_frequency(
    contract_data: dict[str, Any], tmp_path: Path
) -> None:
    def wide_tolerance(data: dict[str, Any]) -> None:
        data["grid"]["tolerance_seconds"] = 150

    error = _broken(contract_data, tmp_path, wide_tolerance)
    assert any("grid.tolerance_seconds: 150 must be less than half" in p for p in error.problems)


def test_grid_column_must_be_a_timestamp(contract_data: dict[str, Any], tmp_path: Path) -> None:
    def wrong_column(data: dict[str, Any]) -> None:
        data["grid"]["column"] = "p_ac_kw"

    error = _broken(contract_data, tmp_path, wrong_column)
    assert "grid.column: 'p_ac_kw' must be a declared timestamp column" in error.problems


def test_device_check_only_on_the_device_column(
    contract_data: dict[str, Any], tmp_path: Path
) -> None:
    def device_check_on_power(data: dict[str, Any]) -> None:
        _rule(data, "unknown_device")["columns"] = ["p_ac_kw"]

    error = _broken(contract_data, tmp_path, device_check_on_power)
    assert any("'known_device' applies only to [dispositivo_id]" in p for p in error.problems)


def test_changelog_must_record_the_current_version(
    contract_data: dict[str, Any], tmp_path: Path
) -> None:
    def unrecorded(data: dict[str, Any]) -> None:
        data["contract_version"] = "1.2.0"

    error = _broken(contract_data, tmp_path, unrecorded)
    assert any(
        "the first entry is 1.1.0, but contract_version is 1.2.0" in p for p in error.problems
    )


def test_non_nullable_column_needs_a_reject_rule(
    contract_data: dict[str, Any], tmp_path: Path
) -> None:
    def unguarded(data: dict[str, Any]) -> None:
        data["quality_rules"] = [r for r in data["quality_rules"] if r["id"] != "missing_key"]

    error = _broken(contract_data, tmp_path, unguarded)
    assert "columns: non-nullable column 'ts' needs a not_null rule with action reject" in (
        error.problems
    )


def test_all_problems_are_reported_together(contract_data: dict[str, Any], tmp_path: Path) -> None:
    def several(data: dict[str, Any]) -> None:
        data["sla"]["freshness"]["timezone"] = "Mars/Base"
        data["devices"][0]["sitio_id"] = "XX-99"

    error = _broken(contract_data, tmp_path, several)
    assert len(error.problems) == 2
    assert str(error).startswith(f"Invalid data contract {tmp_path / 'contract.yaml'}:")


def test_invalid_yaml_fails(tmp_path: Path) -> None:
    path = tmp_path / "contract.yaml"
    path.write_text("columns: [unclosed", encoding="utf-8")
    with pytest.raises(ContractError, match="not valid YAML"):
        load_contract(path)


# --- source header enforcement ----------------------------------------------------


def test_matching_source_header_passes() -> None:
    load_contract(CONTRACT_PATH).check_source_header(SOURCE_HEADER)


def test_renamed_source_column_is_refused() -> None:
    header = ["ts", "dispositivo_id", "potencia_kw", "irradiancia_wm2", "temp_modulo_c"]
    with pytest.raises(ContractError) as excinfo:
        load_contract(CONTRACT_PATH).check_source_header(header)
    assert "source column 'p_ac_kw' is missing" in excinfo.value.problems
    assert any("'potencia_kw' is not in the contract" in p for p in excinfo.value.problems)
