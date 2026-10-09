"""Unit tests for the live replay plan (no database or Grafana needed)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from etl.config import PROJECT_ROOT
from etl.contract import Contract, load_contract
from scripts.replay_live import parse_duration, plan, rows_for, warnings_for

STEP = timedelta(minutes=5)


@pytest.fixture(scope="module")
def contract() -> Contract:
    return load_contract(PROJECT_ROOT / "contracts" / "telemetria.yaml")


def _plan(
    now: datetime, event_in: str = "5m", minutes: int = 20, recovery: int = 15, kind: str = "trip"
):
    return plan(
        now,
        STEP,
        parse_duration(event_in),
        timedelta(minutes=minutes),
        timedelta(minutes=recovery),
        kind,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [("5m", timedelta(minutes=5)), ("300s", timedelta(seconds=300)), ("1h", timedelta(hours=1))],
)
def test_parse_duration(text: str, expected: timedelta) -> None:
    assert parse_duration(text) == expected


def test_event_is_aligned_to_the_next_slot_after_the_delay() -> None:
    scenario = _plan(datetime(2026, 10, 9, 10, 2, 30))
    assert scenario.current_slot == datetime(2026, 10, 9, 10, 0)
    assert scenario.event_start == datetime(2026, 10, 9, 10, 10)  # first slot >= 10:07:30
    assert scenario.event_end == datetime(2026, 10, 9, 10, 30)
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


def test_backfill_starts_after_the_last_reading_already_loaded() -> None:
    scenario = _plan(datetime(2026, 10, 9, 10, 2, 30))
    # A first replay left readings up to 09:30: a second one must not overwrite them.
    assert scenario.backfill_slots(datetime(2026, 10, 9, 9, 30))[0] == datetime(2026, 10, 9, 9, 35)
    # Data from earlier days does not shorten today's backfill.
    assert scenario.backfill_slots(datetime(2026, 10, 7, 23, 55))[0] == datetime(2026, 10, 9, 0, 0)
    assert scenario.backfill_slots(datetime(2026, 10, 9, 10, 0)) == []


def test_trip_rows_have_zero_power_only_during_the_trip() -> None:
    scenario = _plan(datetime(2026, 10, 9, 11, 0))
    rows = rows_for(scenario.live_slots(), scenario, device=1, rng=random.Random(1))
    assert len(rows) == len(scenario.live_slots())
    for row in rows:
        slot = datetime.fromisoformat(str(row[0]))
        assert (row[2] == 0.0) == scenario.in_event(slot)
        assert float(row[3]) > 200  # the sun is up: irradiance stays normal during the trip


def test_gap_sends_nothing_during_the_gap() -> None:
    scenario = _plan(datetime(2026, 10, 9, 11, 0), kind="gap")
    rows = rows_for(scenario.live_slots(), scenario, device=1, rng=random.Random(1))
    sent = {datetime.fromisoformat(str(row[0])) for row in rows}
    gap = {slot for slot in scenario.live_slots() if scenario.in_event(slot)}
    assert len(gap) == 4  # 20 minutes
    assert sent == set(scenario.live_slots()) - gap
    assert all(float(row[2]) > 0 for row in rows)


def test_warns_when_the_event_is_outside_the_daylight_window(contract: Contract) -> None:
    assert warnings_for(contract, _plan(datetime(2026, 10, 9, 10, 0))) == []
    assert warnings_for(contract, _plan(datetime(2026, 10, 9, 10, 0), kind="gap")) == []
    night = warnings_for(contract, _plan(datetime(2026, 10, 9, 21, 0)))
    assert len(night) == 1 and "09:00-15:00" in night[0]
    late = warnings_for(contract, _plan(datetime(2026, 10, 9, 14, 50), kind="gap"))
    assert len(late) == 1  # ends after 15:00


def test_warns_when_the_gap_is_shorter_than_the_contract_outage(contract: Contract) -> None:
    short = warnings_for(contract, _plan(datetime(2026, 10, 9, 10, 0), minutes=10, kind="gap"))
    assert len(short) == 1 and "15 minutos" in short[0]
    exact = warnings_for(contract, _plan(datetime(2026, 10, 9, 10, 0), minutes=15, kind="gap"))
    assert exact == []
