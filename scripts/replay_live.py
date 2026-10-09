"""Live replay of the fault scenarios, to watch the Grafana alerts fire for real.

Alert rules evaluate a window that ends "now", while the simulated data is
historical. This script SIMULATES the IoT feed (there is no real device): it fills
today's readings up to the current 5-minute slot, then writes one small CSV per slot
as wall-clock time passes, each loaded with the normal ETL (Bronze -> Silver -> Gold).
A few minutes from now one of two events happens, then the feed recovers:

- trip (--trip-in, default): the inverter trips (p_ac_kw = 0 with normal irradiance),
  so "Potencia cero en horario solar" goes Normal -> Pending -> Firing -> Normal and
  the webhook receiver gets the payload;
- gap (--gap-in): the inverter or its gateway stops sending (no rows at all), so
  "Inversor sin datos en horario solar" goes Normal -> Firing -> Normal (email route).

The rules only fire inside the contract's daylight window (09:00-15:00 local): run it
during those hours. Files go to data/bronze/live/ (ignored by git). Today's backfill
starts after the device's last reading already in Silver, so a second replay never
overwrites the readings (and the faults) of the first one.

Usage, from the repo root with the stack running
(docker compose --profile alerting up -d):
    python scripts/replay_live.py --trip-in 5m --trip-minutes 20
    python scripts/replay_live.py --gap-in 5m --gap-minutes 20
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

import psycopg

from etl.config import PROJECT_ROOT, ConfigError, Settings, load_settings
from etl.contract import Contract, load_contract
from etl.grafana import GrafanaClient, GrafanaError, load_grafana_settings
from etl.pipeline import EtlRunError, run_etl
from etl.simulador_fallas import HEADER, reading

LIVE_DIR = PROJECT_ROOT / "data" / "bronze" / "live"
# Alert rules shown on every line: uid -> short label.
RULES = {"solarbi-potencia-cero": "potencia cero", "solarbi-sin-datos": "sin datos"}
# Contract fault rule whose daylight window each event needs.
FAULT_RULE = {"trip": "zero_power_daylight", "gap": "missing_daytime_reading"}
EVENT_NAME = {"trip": "disparo", "gap": "corte"}


@dataclass(frozen=True)
class Scenario:
    """Wall-clock plan of the replay, in the source's local (naive) time."""

    now: datetime
    current_slot: datetime
    event_start: datetime
    event_end: datetime
    end: datetime
    step: timedelta
    kind: str = "trip"  # "trip": zero power; "gap": nothing is sent

    def in_event(self, slot: datetime) -> bool:
        return self.event_start <= slot < self.event_end

    def live_slots(self) -> list[datetime]:
        slots, slot = [], self.current_slot + self.step
        while slot <= self.end:
            slots.append(slot)
            slot += self.step
        return slots

    def backfill_slots(self, after: datetime | None = None) -> list[datetime]:
        """Today's slots up to the current one, after the last reading already loaded."""
        slot = self.current_slot.replace(hour=0, minute=0)
        if after is not None and after >= slot:
            slot = after + self.step
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
    now: datetime,
    step: timedelta,
    event_in: timedelta,
    duration: timedelta,
    recovery: timedelta,
    kind: str = "trip",
) -> Scenario:
    """Align to the reading grid: the event starts at the first slot >= now + event_in."""
    seconds = int(step.total_seconds())
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    current = midnight + timedelta(
        seconds=(int((now - midnight).total_seconds()) // seconds) * seconds
    )
    target = now + event_in
    event_start = current
    while event_start < target:
        event_start += step
    event_end = event_start + duration
    return Scenario(
        now=now,
        current_slot=current,
        event_start=event_start,
        event_end=event_end,
        end=event_end + recovery,
        step=step,
        kind=kind,
    )


def rows_for(
    slots: list[datetime], scenario: Scenario, device: int, rng: random.Random
) -> list[list[object]]:
    """Clean readings (no random anomalies): zero power during a trip, none during a gap."""
    rows = []
    for slot in slots:
        row, _ = reading(slot, device, rng)  # always drawn: the sequence does not shift
        if scenario.in_event(slot):
            if scenario.kind == "gap":
                continue
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


def warnings_for(contract: Contract, scenario: Scenario) -> list[str]:
    """Why the alert would NOT fire with this plan (outside daylight, gap too short)."""
    rule = next((r for r in contract.fault_rules if r.id == FAULT_RULE[scenario.kind]), None)
    if rule is None:
        return []
    name = EVENT_NAME[scenario.kind]
    found = []
    window = rule.window
    if not (
        window.contains(scenario.event_start.time())
        and window.contains((scenario.event_end - scenario.step).time())
    ):
        found.append(
            f"AVISO: el {name} ({scenario.event_start:%H:%M}-{scenario.event_end:%H:%M}) queda "
            f"fuera del horario solar de la regla ({window.start:%H:%M}-{window.end:%H:%M} hora "
            "local): los datos se cargan, pero la alerta seguirá en Normal. Ejecútelo entre "
            "esas horas."
        )
    silence = scenario.step * rule.min_consecutive_readings
    if scenario.kind == "gap" and scenario.event_end - scenario.event_start < silence:
        found.append(
            f"AVISO: un corte de menos de {silence.seconds // 60} minutos "
            f"({rule.min_consecutive_readings} lecturas) no es una falla según el contrato: "
            "la alerta no se disparará."
        )
    return found


def last_reading(settings: Settings, device: int, contract: Contract) -> datetime | None:
    """Latest reading of the device already in Silver, in the source's local naive time."""
    with psycopg.connect(settings.db.url, connect_timeout=10) as conn:
        row = conn.execute(
            "SELECT max(ts) FROM silver.lectura_5min WHERE dispositivo_id = %s", (str(device),)
        ).fetchone()
    if row is None or row[0] is None:
        return None
    return row[0].astimezone(contract.timezone).replace(tzinfo=None)


def alert_states(client: GrafanaClient | None) -> str:
    if client is None:
        return "?"
    try:
        groups = client.get("/api/prometheus/grafana/api/v1/rules")["data"]["groups"]
    except GrafanaError:
        return "? (Grafana no responde)"
    states = {}
    for group in groups:
        for rule in group["rules"]:
            label = RULES.get(rule.get("uid", ""))
            if label is None:
                continue
            instances = ", ".join(
                f"{a['labels'].get('dispositivo', '?')}={a['state']}"
                for a in rule.get("alerts", [])
            )
            states[label] = f"{label} {rule['state']} [{instances or 'sin instancias'}]"
    return " · ".join(states.get(label, f"{label} no encontrada") for label in RULES.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live replay of the inverter fault scenarios.")
    event = parser.add_mutually_exclusive_group()
    event.add_argument("--trip-in", type=parse_duration, help="trip after this delay (default 5m)")
    event.add_argument("--gap-in", type=parse_duration, help="stop sending after this delay")
    parser.add_argument("--trip-minutes", type=int, default=20)
    parser.add_argument("--gap-minutes", type=int, default=20)
    parser.add_argument("--recovery-minutes", type=int, default=15)
    parser.add_argument("--device", type=int, default=1)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--no-backfill", action="store_true", help="skip today's readings up to now"
    )
    args = parser.parse_args(argv)
    if args.gap_in is not None:
        kind, event_in, minutes = "gap", args.gap_in, args.gap_minutes
    else:
        kind, event_in, minutes = "trip", args.trip_in or parse_duration("5m"), args.trip_minutes

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
        event_in,
        timedelta(minutes=minutes),
        timedelta(minutes=args.recovery_minutes),
        kind,
    )
    rng = random.Random(args.seed)
    name = EVENT_NAME[kind]
    print("Réplica en vivo (simula el envío IoT; no hay un dispositivo real)")
    print(f"  ahora {scenario.now:%Y-%m-%d %H:%M:%S} ({tz.key}); dispositivo {args.device}")
    detail = " (no se envía nada)" if kind == "gap" else " (potencia 0 con irradiancia normal)"
    print(
        f"  {name} {scenario.event_start:%H:%M}-{scenario.event_end:%H:%M}{detail}; "
        f"fin {scenario.end:%H:%M}"
    )
    for warning in warnings_for(contract, scenario):
        print(f"  {warning}")

    def load(rows: list[list[object]], file_name: str) -> str:
        path = write_batch(rows, file_name)
        try:
            report = run_etl(settings, path, log=lambda _message: None)
        except EtlRunError as exc:
            return f"ETL FALLÓ ({exc.cause})"
        counts = f"{report.filas_validas}/{report.filas_leidas} válidas"
        return f"ETL ok ({counts}, {report.eventos_falla} eventos)"

    if not args.no_backfill:
        after = last_reading(settings, args.device, contract)
        slots = scenario.backfill_slots(after)
        if slots:
            status = load(
                rows_for(slots, scenario, args.device, rng),
                f"replay_{scenario.now:%Y%m%d_%H%M%S}_backfill.csv",
            )
            print(
                f"[{scenario.now:%H:%M:%S}] respaldo de hoy {slots[0]:%H:%M}-{slots[-1]:%H:%M}: "
                f"{len(slots)} lecturas | {status} | alertas: {alert_states(grafana)}"
            )
        else:
            print(f"[{scenario.now:%H:%M:%S}] respaldo: hoy ya está cargado hasta ahora")

    try:
        for slot in scenario.live_slots():
            wait = (slot - datetime.now(tz).replace(tzinfo=None)).total_seconds()
            if wait > 0:
                time.sleep(wait)
            rows = rows_for([slot], scenario, args.device, rng)
            if rows:
                status = load(rows, f"replay_{slot:%Y%m%d_%H%M}.csv")
                tag = "DISPARO " if scenario.in_event(slot) else "        "
                sent = f"{tag}p_ac_kw={rows[0][2]:<6} irr={rows[0][3]:<6} | {status}"
            else:
                sent = "CORTE    sin envío (el inversor no reporta)"
            print(
                f"[{datetime.now(tz):%H:%M:%S}] lectura {slot:%H:%M} {sent} | "
                f"alertas: {alert_states(grafana)}",
                flush=True,
            )
    except KeyboardInterrupt:
        print("Detenido por el usuario.")
    print(f"Fin de la réplica. Estado final: {alert_states(grafana)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
