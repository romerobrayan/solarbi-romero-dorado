"""Extended telemetry simulator: reproducible, configurable, with optional plant faults.

Same physics and anomaly rates as the professor's etl/simulador.py (negative power ~2 %,
missing irradiance ~2 %, duplicated rows ~2 %), plus:

- a fixed random seed (--seed), so the same arguments always produce the same file;
- --days, --devices, --start and --out;
- --inject-faults (off by default): an inverter trip (p_ac_kw = 0 for 40 minutes around
  midday with normal irradiance, on the second day) and a 20-minute communication gap
  (missing rows, on the last day), so the contract's fault rules have something to detect.

Usage, from the repository root:
    python etl/simulador_fallas.py --seed 42 --inject-faults

The default output is data/samples/telemetria_fallas_seed<seed>.csv; it never overwrites
data/bronze/telemetria.csv unless --out says so.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

READINGS_PER_DAY = 288  # one every 5 minutes
STEP = timedelta(minutes=5)
NOMINAL_KWP = 5.0
HEADER = ["ts", "dispositivo_id", "p_ac_kw", "irradiancia_wm2", "temp_modulo_c"]

# Fault windows, in local clock time (the file has no time zone, like the professor's).
TRIP_DAY_INDEX = 1  # second day
TRIP_START, TRIP_END = time(12, 0), time(12, 40)  # 8 readings at zero power
GAP_START, GAP_END = time(10, 0), time(10, 20)  # 4 readings never sent


@dataclass(frozen=True)
class SimulationConfig:
    seed: int = 42
    days: int = 3
    devices: int = 1
    start: date = date(2026, 10, 2)
    inject_faults: bool = False


def reading(ts: datetime, device: int, rng: random.Random) -> tuple[list[object], float]:
    """One healthy reading (same physics as the professor's simulator) and the random
    draw that decides its anomaly. Also used by scripts/replay_live.py."""
    sol = max(0.0, math.sin(math.pi * (ts.hour + ts.minute / 60 - 6) / 12))
    irr = round(1000 * sol * rng.uniform(0.7, 1.0), 1)
    p_ac = round(NOMINAL_KWP * irr / 1000 * rng.uniform(0.80, 0.90), 3)
    row: list[object] = [ts.isoformat(sep=" "), device, p_ac, irr, round(22 + 30 * sol, 1)]
    return row, rng.random()


def generate_rows(config: SimulationConfig) -> list[list[object]]:
    """All data rows (without header), in time order, device by device per timestamp."""
    rng = random.Random(config.seed)
    rows: list[list[object]] = []
    start = datetime.combine(config.start, time(0, 0))
    for i in range(config.days * READINGS_PER_DAY):
        ts = start + STEP * i
        day_index = i // READINGS_PER_DAY
        for device in range(1, config.devices + 1):
            in_trip = (
                config.inject_faults
                and day_index == TRIP_DAY_INDEX
                and _within(ts, TRIP_START, TRIP_END)
            )
            in_gap = (
                config.inject_faults
                and day_index == config.days - 1
                and _within(ts, GAP_START, GAP_END)
            )
            # r is always drawn, so faults do not shift the random sequence
            row, r = reading(ts, device, rng)
            if in_gap:
                continue  # the inverter or its link stopped reporting
            if in_trip:
                row[2] = 0.0  # inverter tripped: no power with normal irradiance
                rows.append(row)
                continue  # keep the fault clean of random anomalies
            if r < 0.02:
                row[2] = -1  # anomaly: negative power
            elif r < 0.04:
                row[3] = ""  # anomaly: missing irradiance
            rows.append(row)
            if r > 0.98:
                rows.append(list(row))  # anomaly: duplicated row
    return rows


def write_csv(path: Path, rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerows(rows)


def _within(ts: datetime, start: time, end: time) -> bool:
    return start <= ts.time() < end


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SolarBI telemetry simulator with faults.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--devices", type=int, default=1)
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=SimulationConfig.start,
        help="first day, YYYY-MM-DD (default 2026-10-02: the 3 days before the professor's file)",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--inject-faults", action="store_true")
    args = parser.parse_args(argv)

    config = SimulationConfig(
        seed=args.seed,
        days=args.days,
        devices=args.devices,
        start=args.start,
        inject_faults=args.inject_faults,
    )
    out = args.out or Path("data/samples") / f"telemetria_fallas_seed{config.seed}.csv"
    rows = generate_rows(config)
    write_csv(out, rows)
    print(f"{len(rows)} filas escritas en {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
