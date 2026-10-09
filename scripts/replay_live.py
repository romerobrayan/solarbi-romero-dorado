"""Live replay of the fault scenario, to watch the Grafana alert fire for real.

Alert rules evaluate a window that ends "now", while the simulated data is
historical. This script SIMULATES the IoT feed (there is no real device): it
writes today's readings up to the current 5-minute slot, then one small CSV per
slot as wall-clock time passes, each loaded with the normal ETL
(Bronze -> Silver -> Gold). The inverter trips a few minutes from now
(p_ac_kw = 0 with normal irradiance) and later recovers, so the rule goes
Normal -> Pending -> Firing -> Normal, and the webhook receiver gets the payload.

The rule only fires inside the contract's daylight window (09:00-15:00 local):
run it during those hours. Files go to data/bronze/live/ (ignored by git).

Usage, from the repo root with the stack running
(docker compose --profile alerting up -d):
    python scripts/replay_live.py --trip-in 5m --trip-minutes 20
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from etl.config import PROJECT_ROOT, ConfigError, load_settings
from etl.contract import Contract, load_contract
from etl.grafana import GrafanaClient, GrafanaError, load_grafana_settings
from etl.pipeline import EtlRunError, run_etl
from etl.simulador_fallas import HEADER, reading

LIVE_DIR = PROJECT_ROOT / "data" / "bronze" / "live"
RULE_UID = "solarbi-potencia-cero"
FAULT_RULE = "zero_power_daylight"


@dataclass(frozen=True)
class Scenario:
    """Wall-clock plan of the replay, in the source's local (naive) time."""

    now: datetime
    current_slot: datetime
    trip_start: datetime
    trip_end: datetime
    end: datetime
    step: timedelta

    def is_trip(self, slot: datetime) -> bool:
        return self.trip_start <= slot < self.trip_end

    def live_slots(self) -> list[datetime]:
        slots, slot = [], self.current_slot + self.step
        while slot <= self.end:
            slots.append(slot)
            slot += self.step
        return slots

    def backfill_slots(self) -> list[datetime]:
        slot = self.current_slot.replace(hour=0, minute=0)
        slots = []
        while slot <= self.current_slot:
            slots.append(slot)
            slot += self.step
        return slots


def parse_duration(text: str) -> timedelta:
    match = re.fullmatch(r"(\d+)([smh])", text.strip())
    if not match:
        raise argparse.ArgumentTypeError(f"duration like 5m, 300s or 1h expected, got {text!r}")
    value, unit = int(match[1]), match[2]
    unit_name = {"s": "seconds", "m": "minutes", "h": "hours"}[unit]
    return timedelta(**{unit_name: value})


def plan(
    now: datetime, step: timedelta, trip_in: timedelta, trip: timedelta, recovery: timedelta
) -> Scenario:
    """Align everything to the reading grid: the trip starts at the first slot >= now + trip_in."""
    seconds = int(step.total_seconds())
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    current = midnight + timedelta(
        seconds=(int((now - midnight).total_seconds()) // seconds) * seconds
    )
    target = now + trip_in
    trip_start = current
    while trip_start < target:
        trip_start += step
    trip_end = trip_start + trip
    return Scenario(
        now=now,
        current_slot=current,
        trip_start=trip_start,
        trip_end=trip_end,
        end=trip_end + recovery,
        step=step,
    )


def rows_for(
    slots: list[datetime], scenario: Scenario, device: int, rng: random.Random
) -> list[list[object]]:
    """Clean readings (no random anomalies); zero power during the trip."""
    rows = []
    for slot in slots:
        row, _ = reading(slot, device, rng)
        if scenario.is_trip(slot):
            row[2] = 0.0
        rows.append(row)
    return rows


def write_batch(rows: list[list[object]], name: str) -> Path:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = LIVE_DIR / name
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerows(rows)
    return path


def daylight_warning(contract: Contract, scenario: Scenario) -> str | None:
    rule = next((r for r in contract.fault_rules if r.id == FAULT_RULE), None)
    if rule is None:
        return None
    window = rule.window
    if window.contains(scenario.trip_start.time()) and window.contains(
        (scenario.trip_end - scenario.step).time()
    ):
        return None
    return (
        f"AVISO: el disparo ({scenario.trip_start:%H:%M}-{scenario.trip_end:%H:%M}) queda fuera "
        f"del horario solar de la regla ({window.start:%H:%M}-{window.end:%H:%M} hora local): "
        "los datos se cargan, pero la alerta seguirá en Normal. Ejecútelo entre esas horas."
    )


def alert_state(client: GrafanaClient | None) -> str:
    if client is None:
        return "?"
    try:
        groups = client.get("/api/prometheus/grafana/api/v1/rules")["data"]["groups"]
    except GrafanaError:
        return "? (Grafana no responde)"
    for group in groups:
        for rule in group["rules"]:
            if rule.get("uid") == RULE_UID or rule["name"] == "Potencia cero en horario solar":
                instances = ", ".join(
                    f"{a['labels'].get('dispositivo', '?')}={a['state']}"
                    for a in rule.get("alerts", [])
                )
                return f"{rule['state']} [{instances or 'sin instancias'}]"
    return "regla no encontrada"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live replay of the inverter-trip scenario.")
    parser.add_argument("--trip-in", type=parse_duration, default=parse_duration("5m"))
    parser.add_argument("--trip-minutes", type=int, default=20)
    parser.add_argument("--recovery-minutes", type=int, default=15)
    parser.add_argument("--device", type=int, default=1)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--no-backfill", action="store_true", help="skip today's readings up to now"
    )
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        contract = load_contract(settings.paths.contracts_dir / "telemetria.yaml")
    except ConfigError as exc:
        print(f"ERROR DE CONFIGURACIÓN: {exc}", file=sys.stderr)
        return 2
    try:
        grafana: GrafanaClient | None = GrafanaClient(load_grafana_settings())
    except ConfigError:
        grafana = None

    tz = contract.timezone
    step = timedelta(seconds=contract.frequency_seconds)
    scenario = plan(
        datetime.now(tz).replace(tzinfo=None),
        step,
        args.trip_in,
        timedelta(minutes=args.trip_minutes),
        timedelta(minutes=args.recovery_minutes),
    )
    rng = random.Random(args.seed)
    print("Réplica en vivo (simula el envío IoT; no hay un dispositivo real)")
    print(f"  ahora {scenario.now:%Y-%m-%d %H:%M:%S} ({tz.key}); dispositivo {args.device}")
    print(
        f"  disparo {scenario.trip_start:%H:%M}-{scenario.trip_end:%H:%M}; fin {scenario.end:%H:%M}"
    )
    warning = daylight_warning(contract, scenario)
    if warning:
        print(f"  {warning}")

    def load(rows: list[list[object]], name: str) -> str:
        path = write_batch(rows, name)
        try:
            report = run_etl(settings, path, log=lambda _message: None)
        except EtlRunError as exc:
            return f"ETL FALLÓ ({exc.cause})"
        counts = f"{report.filas_validas}/{report.filas_leidas} válidas"
        return f"ETL ok ({counts}, {report.eventos_falla} eventos)"

    if not args.no_backfill:
        slots = scenario.backfill_slots()
        status = load(
            rows_for(slots, scenario, args.device, rng),
            f"replay_{scenario.now:%Y%m%d_%H%M%S}_backfill.csv",
        )
        print(
            f"[{scenario.now:%H:%M:%S}] respaldo de hoy hasta {scenario.current_slot:%H:%M}: "
            f"{len(slots)} lecturas | {status} | alerta: {alert_state(grafana)}"
        )

    try:
        for slot in scenario.live_slots():
            wait = (slot - datetime.now(tz).replace(tzinfo=None)).total_seconds()
            if wait > 0:
                time.sleep(wait)
            rows = rows_for([slot], scenario, args.device, rng)
            status = load(rows, f"replay_{slot:%Y%m%d_%H%M}.csv")
            tag = "DISPARO " if scenario.is_trip(slot) else "        "
            print(
                f"[{datetime.now(tz):%H:%M:%S}] lectura {slot:%H:%M} {tag}p_ac_kw={rows[0][2]:<6} "
                f"irr={rows[0][3]:<6} | {status} | alerta: {alert_state(grafana)}",
                flush=True,
            )
    except KeyboardInterrupt:
        print("Detenido por el usuario.")
    print(f"Fin de la réplica. Estado final de la alerta: {alert_state(grafana)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
