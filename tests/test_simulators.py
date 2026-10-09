"""Tests for the professor's simulator and the extended one (no database needed)."""

from __future__ import annotations

import csv
import subprocess
import sys
from datetime import date
from pathlib import Path

from etl.config import PROJECT_ROOT
from etl.simulador_fallas import HEADER, SimulationConfig, generate_rows, main

PROFESSOR_SIMULATOR = PROJECT_ROOT / "etl" / "simulador.py"


def _read(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


def test_professor_simulator_writes_three_days_of_bronze(tmp_path: Path) -> None:
    subprocess.run([sys.executable, str(PROFESSOR_SIMULATOR)], cwd=tmp_path, check=True)
    rows = _read(tmp_path / "data" / "bronze" / "telemetria.csv")
    header, data = rows[0], rows[1:]
    assert header == HEADER
    timestamps = {row[0] for row in data}
    assert len(timestamps) == 3 * 288  # every slot present; extra rows are duplicates
    assert min(timestamps) == "2026-10-05 00:00:00"
    assert max(timestamps) == "2026-10-07 23:55:00"


def test_same_seed_same_rows() -> None:
    config = SimulationConfig(seed=42, inject_faults=True)
    assert generate_rows(config) == generate_rows(config)
    assert generate_rows(config) != generate_rows(SimulationConfig(seed=43, inject_faults=True))


def test_faults_are_off_by_default_and_injected_on_request() -> None:
    normal = generate_rows(SimulationConfig(seed=1, start=date(2030, 1, 1)))
    faulty = generate_rows(SimulationConfig(seed=1, start=date(2030, 1, 1), inject_faults=True))

    def trip(rows: list[list[object]]) -> list[list[object]]:
        return [r for r in rows if "2030-01-02 12:00:00" <= str(r[0]) < "2030-01-02 12:40:00"]

    assert all(float(r[2]) > 0 for r in trip(normal))
    tripped = trip(faulty)
    assert len(tripped) == 8
    assert all(r[2] == 0.0 and float(r[3]) > 200 for r in tripped)  # no power, normal sun
    gap = [r for r in faulty if "2030-01-03 10:00:00" <= str(r[0]) < "2030-01-03 10:20:00"]
    assert gap == []


def test_default_output_never_overwrites_bronze(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    assert main(["--seed", "5", "--days", "1"]) == 0
    assert (tmp_path / "data" / "samples" / "telemetria_fallas_seed5.csv").is_file()
    assert not (tmp_path / "data" / "bronze").exists()


def test_committed_fault_sample_is_the_default_output_and_in_the_past(tmp_path: Path) -> None:
    """data/samples is reproducible, and dated before the professor's file (no future data)."""
    committed = PROJECT_ROOT / "data" / "samples" / "telemetria_fallas_seed42.csv"
    out = tmp_path / "sample.csv"
    assert main(["--seed", "42", "--inject-faults", "--out", str(out)]) == 0
    assert out.read_bytes() == committed.read_bytes()
    timestamps = [row[0] for row in _read(committed)[1:]]
    assert (min(timestamps), max(timestamps)) == ("2026-10-02 00:00:00", "2026-10-04 23:55:00")
