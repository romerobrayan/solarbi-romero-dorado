"""Unit tests for the live replay plan (no database or Grafana needed)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from etl.config import PROJECT_ROOT
from etl.contract import load_contract
from scripts.replay_live import daylight_warning, parse_duration, plan, rows_for

STEP = timedelta(minutes=5)


def _plan(now: datetime, trip_in: str = "5m", trip: int = 20, recovery: int = 15):
    return plan(
        now, STEP, parse_duration(trip_in), timedelta(minutes=trip), timedelta(minutes=recovery)
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [("5m", timedelta(minutes=5)), ("300s", timedelta(seconds=300)), ("1h", timedelta(hours=1))],
)
def test_parse_duration(text: str, expected: timedelta) -> None:
    assert parse_duration(text) == expected


def test_trip_is_aligned_to_the_next_slot_after_the_delay() -> None:
    scenario = _plan(datetime(2026, 10, 9, 10, 2, 30))
    assert scenario.current_slot == datetime(2026, 10, 9, 10, 0)
    assert scenario.trip_start == datetime(2026, 10, 9, 10, 10)  # first slot >= 10:07:30
    assert scenario.trip_end == datetime(2026, 10, 9, 10, 30)
    assert scenario.end == datetime(2026, 10, 9, 10, 45)


def test_slots_backfill_today_and_then_follow_the_clock() -> None:
    scenario = _plan(datetime(2026, 10, 9, 10, 2, 30))
    backfill = scenario.backfill_slots()
    assert backfill[0] == datetime(2026, 10, 9, 0, 0)
    assert backfill[-1] == datetime(2026, 10, 9, 10, 0)
    assert len(backfill) == 121
    live = scenario.live_slots()
    assert live[0] == datetime(2026, 10, 9, 10, 5)
    assert live[-1] == scenario.end


def test_rows_have_zero_power_only_during_the_trip() -> None:
    scenario = _plan(datetime(2026, 10, 9, 11, 0))
    rows = rows_for(scenario.live_slots(), scenario, device=1, rng=random.Random(1))
    for row in rows:
        slot = datetime.fromisoformat(str(row[0]))
        assert (row[2] == 0.0) == scenario.is_trip(slot)
        assert float(row[3]) > 200  # the sun is up: irradiance stays normal during the trip


def test_warns_when_the_trip_is_outside_the_daylight_window() -> None:
    contract = load_contract(PROJECT_ROOT / "contracts" / "telemetria.yaml")
    assert daylight_warning(contract, _plan(datetime(2026, 10, 9, 10, 0))) is None
    night = daylight_warning(contract, _plan(datetime(2026, 10, 9, 21, 0)))
    assert night is not None and "09:00-15:00" in night
    late = daylight_warning(contract, _plan(datetime(2026, 10, 9, 14, 50)))  # ends after 15:00
    assert late is not None
