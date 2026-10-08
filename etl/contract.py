"""Data contract: load, validate and expose a dataset definition from contracts/*.yaml.

Validation has two layers:

1. Structure, checked against the JSON Schema (contracts/telemetria.schema.json).
2. Semantics JSON Schema cannot express: references between sections, ranges,
   time zones, time windows and the supported major version.

Every problem is collected and raised together in one ContractError, so a broken
contract can be fixed in a single pass. Source column names live only in the
contract; code works with the canonical (Silver) names.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from jsonschema import Draft202012Validator

from etl.config import PROJECT_ROOT

SUPPORTED_MAJOR_VERSION = 1
SCHEMA_PATH = PROJECT_ROOT / "contracts" / "telemetria.schema.json"
SECONDS_PER_DAY = 86_400

# Canonical columns the pipeline cannot work without: the natural key and the
# power used for energy and fault detection.
REQUIRED_COLUMNS = ("ts", "dispositivo_id", "p_ac_kw")
NUMERIC_TYPES = ("integer", "float")


class ContractError(ValueError):
    """The contract file (or a source file checked against it) is not valid."""

    def __init__(self, source: Path | str, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        details = "\n".join(f"  - {problem}" for problem in self.problems)
        super().__init__(f"Invalid data contract {source}:\n{details}")


@dataclass(frozen=True)
class ValueRange:
    min: float | None = None
    max: float | None = None
    max_nominal_factor: float | None = None  # max = factor x nominal_kwp of the device


@dataclass(frozen=True)
class Column:
    name: str
    source_name: str | None  # None: the source does not provide it (filled with NULL)
    type: str
    unit: str | None
    nullable: bool
    description: str
    range: ValueRange | None


@dataclass(frozen=True)
class SourceSpec:
    format: str
    path_pattern: str
    delimiter: str
    encoding: str
    header: bool
    null_values: tuple[str, ...]
    timestamp_format: str
    timezone: ZoneInfo
    frequency_seconds: int
    allow_extra_columns: bool


@dataclass(frozen=True)
class QualityRule:
    id: str
    description: str
    columns: tuple[str, ...]
    check: str
    action: str


@dataclass(frozen=True)
class TimeWindow:
    """Local clock-time window, half-open: start <= t < end."""

    start: time
    end: time

    def contains(self, moment: time) -> bool:
        return self.start <= moment < self.end


@dataclass(frozen=True)
class IrradianceGate:
    enabled: bool
    column: str
    operator: str
    threshold: float


@dataclass(frozen=True)
class FaultRule:
    id: str
    description: str
    check: str
    window: TimeWindow
    min_consecutive_readings: int
    severity: str
    column: str | None = None
    operator: str | None = None
    threshold: float | None = None
    irradiance_gate: IrradianceGate | None = None


@dataclass(frozen=True)
class Site:
    sitio_id: str
    nombre: str
    ciudad: str | None
    timezone: ZoneInfo


@dataclass(frozen=True)
class Device:
    dispositivo_id: str
    nombre: str
    tipo: str | None
    sitio_id: str
    nominal_kwp: float


@dataclass(frozen=True)
class Sla:
    pct_datos_validos_min: float
    schedule_cron: str
    timezone: ZoneInfo
    max_delay_minutes: int


@dataclass(frozen=True)
class Contract:
    path: Path
    version: str
    dataset: str
    source: SourceSpec
    columns: tuple[Column, ...]
    natural_key: tuple[str, ...]
    dedup_keep: str
    quality_rules: tuple[QualityRule, ...]
    fault_rules: tuple[FaultRule, ...]
    sites: tuple[Site, ...]
    devices: tuple[Device, ...]
    sla: Sla

    @property
    def major_version(self) -> int:
        return int(self.version.split(".")[0])

    @property
    def timezone(self) -> ZoneInfo:
        """Time zone of the naive timestamps in the source file."""
        return self.source.timezone

    @property
    def frequency_seconds(self) -> int:
        return self.source.frequency_seconds

    @property
    def interval_hours(self) -> float:
        """Length of one reading in hours: energy_kwh = power_kw x interval_hours."""
        return self.source.frequency_seconds / 3600

    @property
    def expected_readings_per_day(self) -> int:
        return SECONDS_PER_DAY // self.source.frequency_seconds

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def source_columns(self) -> tuple[str, ...]:
        """Column names expected in the source file header, in contract order."""
        return tuple(c.source_name for c in self.columns if c.source_name is not None)

    @property
    def source_to_canonical(self) -> dict[str, str]:
        return {c.source_name: c.name for c in self.columns if c.source_name is not None}

    def column(self, name: str) -> Column:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(f"column {name!r} is not declared in contract {self.path.name}")

    def device(self, dispositivo_id: str) -> Device:
        for device in self.devices:
            if device.dispositivo_id == dispositivo_id:
                return device
        raise KeyError(f"device {dispositivo_id!r} is not declared in contract {self.path.name}")

    def rules_with_action(self, action: str) -> tuple[QualityRule, ...]:
        return tuple(rule for rule in self.quality_rules if rule.action == action)

    def max_value(self, column: str, dispositivo_id: str | None = None) -> float | None:
        """Upper bound of a column, resolving max_nominal_factor against the device."""
        value_range = self.column(column).range
        if value_range is None:
            return None
        if value_range.max_nominal_factor is None:
            return value_range.max
        if dispositivo_id is None:
            raise ValueError(f"the maximum of {column!r} depends on the device; pass one")
        return value_range.max_nominal_factor * self.device(dispositivo_id).nominal_kwp

    def check_source_header(self, header: Sequence[str]) -> None:
        """Refuse a source file whose columns do not match the contract."""
        expected = self.source_columns
        problems = [f"source column {name!r} is missing" for name in expected if name not in header]
        problems += [
            f"source column {name!r} appears {count} times"
            for name, count in Counter(header).items()
            if count > 1
        ]
        unexpected = [name for name in header if name not in expected]
        if unexpected and not self.source.allow_extra_columns:
            problems += [
                f"source column {name!r} is not in the contract "
                "(announce it and publish a new contract version)"
                for name in unexpected
            ]
        if problems:
            raise ContractError(f"{self.path.name} v{self.version} vs source header", problems)


def load_contract(path: Path, schema_path: Path = SCHEMA_PATH) -> Contract:
    """Read, validate and return the contract; raise ContractError on any problem."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractError(path, [f"cannot read file: {exc}"]) from exc
    except yaml.YAMLError as exc:
        raise ContractError(path, [f"not valid YAML: {exc}"]) from exc

    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    problems = _schema_problems(data, schema)
    if not problems:  # semantic checks assume a well-formed document
        problems = _semantic_problems(data)
    if problems:
        raise ContractError(path, problems)
    return _build(path, data)


# --- validation ---------------------------------------------------------------


def _schema_problems(data: Any, schema: Mapping[str, Any]) -> list[str]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    return [f"{_location(error.absolute_path)}: {error.message}" for error in errors]


def _location(path: Iterable[Any]) -> str:
    text = ""
    for part in path:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else str(part))
    return text or "(root)"


def _semantic_problems(data: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []

    major = int(data["contract_version"].split(".")[0])
    if major != SUPPORTED_MAJOR_VERSION:
        problems.append(
            f"contract_version: major version {major} is not supported by this ETL "
            f"(expects {SUPPORTED_MAJOR_VERSION}.x.x)"
        )

    source = data["source"]
    problems += _timezone_problems("source.source_timezone", source["source_timezone"])
    if SECONDS_PER_DAY % source["frequency_seconds"]:
        problems.append(
            f"source.frequency_seconds: {source['frequency_seconds']} does not divide a day "
            f"({SECONDS_PER_DAY} s) into whole readings"
        )

    columns = {column["name"]: column for column in data["columns"]}
    problems += _duplicates("columns", [c["name"] for c in data["columns"]])
    problems += _duplicates(
        "columns[].source_name",
        [c["source_name"] for c in data["columns"] if c["source_name"] is not None],
    )
    problems += [
        f"columns: required column {name!r} is missing"
        for name in REQUIRED_COLUMNS
        if name not in columns
    ]
    for index, column in enumerate(data["columns"]):
        problems += _column_problems(f"columns[{index}] ({column['name']})", column)

    natural_key = data["keys"]["natural_key"]
    for name in natural_key:
        if name not in columns:
            problems.append(f"keys.natural_key: column {name!r} is not declared in columns")
        elif columns[name]["nullable"]:
            problems.append(f"keys.natural_key: column {name!r} must not be nullable")

    rule_ids = [r["id"] for r in data["quality_rules"]] + [r["id"] for r in data["fault_rules"]]
    problems += _duplicates("quality_rules/fault_rules ids", rule_ids)
    for index, rule in enumerate(data["quality_rules"]):
        problems += _quality_rule_problems(f"quality_rules[{index}] ({rule['id']})", rule, columns)
    for index, rule in enumerate(data["fault_rules"]):
        problems += _fault_rule_problems(f"fault_rules[{index}] ({rule['id']})", rule, columns)

    site_ids = [site["sitio_id"] for site in data["sites"]]
    problems += _duplicates("sites", site_ids)
    for index, site in enumerate(data["sites"]):
        problems += _timezone_problems(f"sites[{index}].zona_horaria", site["zona_horaria"])
    problems += _duplicates("devices", [d["dispositivo_id"] for d in data["devices"]])
    for index, device in enumerate(data["devices"]):
        if device["sitio_id"] not in site_ids:
            problems.append(
                f"devices[{index}]: sitio_id {device['sitio_id']!r} is not declared in sites"
            )

    problems += _timezone_problems("sla.freshness.timezone", data["sla"]["freshness"]["timezone"])
    return problems


def _column_problems(where: str, column: Mapping[str, Any]) -> list[str]:
    problems = []
    if column["source_name"] is None and not column["nullable"]:
        problems.append(f"{where}: a non-nullable column needs a source_name")
    value_range = column.get("range")
    if value_range is None:
        return problems
    if column["type"] not in NUMERIC_TYPES:
        problems.append(f"{where}: range is only allowed on numeric columns")
    if "max" in value_range and "max_nominal_factor" in value_range:
        problems.append(f"{where}: range has both max and max_nominal_factor; keep one")
    low, high = value_range.get("min"), value_range.get("max")
    if low is not None and high is not None and low >= high:
        problems.append(f"{where}: range min ({low}) must be lower than max ({high})")
    return problems


def _quality_rule_problems(
    where: str, rule: Mapping[str, Any], columns: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    problems = [
        f"{where}: column {name!r} is not declared in columns"
        for name in rule["columns"]
        if name not in columns
    ]
    if (rule["check"] == "unique") != (rule["action"] == "dedupe"):
        problems.append(f"{where}: check 'unique' and action 'dedupe' go together")
    if rule["check"] == "range":
        problems += [
            f"{where}: column {name!r} has no range to check"
            for name in rule["columns"]
            if name in columns and columns[name].get("range") is None
        ]
    return problems


def _fault_rule_problems(
    where: str, rule: Mapping[str, Any], columns: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    problems = []
    window = rule["window"]
    if window["start"] >= window["end"]:  # zero-padded HH:MM compares correctly as text
        problems.append(
            f"{where}: window start {window['start']} must be before end {window['end']}"
        )
    referenced = [rule.get("column")]
    gate = rule.get("irradiance_gate")
    if gate is not None:
        referenced.append(gate["column"])
    problems += [
        f"{where}: column {name!r} is not declared in columns"
        for name in referenced
        if name is not None and name not in columns
    ]
    return problems


def _timezone_problems(where: str, name: str) -> list[str]:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return [f"{where}: unknown time zone {name!r} (use an IANA name such as America/Bogota)"]
    return []


def _duplicates(where: str, values: Sequence[str]) -> list[str]:
    return [
        f"{where}: {value!r} is declared {count} times"
        for value, count in Counter(values).items()
        if count > 1
    ]


# --- construction -------------------------------------------------------------


def _build(path: Path, data: Mapping[str, Any]) -> Contract:
    source = data["source"]
    freshness = data["sla"]["freshness"]
    return Contract(
        path=path,
        version=data["contract_version"],
        dataset=data["dataset"],
        source=SourceSpec(
            format=source["format"],
            path_pattern=source["path_pattern"],
            delimiter=source["delimiter"],
            encoding=source["encoding"],
            header=source["header"],
            null_values=tuple(source.get("null_values", [""])),
            timestamp_format=source["timestamp_format"],
            timezone=ZoneInfo(source["source_timezone"]),
            frequency_seconds=source["frequency_seconds"],
            allow_extra_columns=source.get("allow_extra_columns", False),
        ),
        columns=tuple(_build_column(column) for column in data["columns"]),
        natural_key=tuple(data["keys"]["natural_key"]),
        dedup_keep=data["keys"]["dedup"]["keep"],
        quality_rules=tuple(
            QualityRule(
                id=rule["id"],
                description=rule["description"],
                columns=tuple(rule["columns"]),
                check=rule["check"],
                action=rule["action"],
            )
            for rule in data["quality_rules"]
        ),
        fault_rules=tuple(_build_fault_rule(rule) for rule in data["fault_rules"]),
        sites=tuple(
            Site(
                sitio_id=site["sitio_id"],
                nombre=site["nombre"],
                ciudad=site.get("ciudad"),
                timezone=ZoneInfo(site["zona_horaria"]),
            )
            for site in data["sites"]
        ),
        devices=tuple(
            Device(
                dispositivo_id=device["dispositivo_id"],
                nombre=device["nombre"],
                tipo=device.get("tipo"),
                sitio_id=device["sitio_id"],
                nominal_kwp=float(device["nominal_kwp"]),
            )
            for device in data["devices"]
        ),
        sla=Sla(
            pct_datos_validos_min=float(data["sla"]["pct_datos_validos_min"]),
            schedule_cron=freshness["schedule_cron"],
            timezone=ZoneInfo(freshness["timezone"]),
            max_delay_minutes=freshness["max_delay_minutes"],
        ),
    )


def _build_column(column: Mapping[str, Any]) -> Column:
    value_range = column.get("range")
    return Column(
        name=column["name"],
        source_name=column["source_name"],
        type=column["type"],
        unit=column.get("unit"),
        nullable=column["nullable"],
        description=column["description"],
        range=None
        if value_range is None
        else ValueRange(
            min=_float_or_none(value_range.get("min")),
            max=_float_or_none(value_range.get("max")),
            max_nominal_factor=_float_or_none(value_range.get("max_nominal_factor")),
        ),
    )


def _build_fault_rule(rule: Mapping[str, Any]) -> FaultRule:
    gate = rule.get("irradiance_gate")
    return FaultRule(
        id=rule["id"],
        description=rule["description"],
        check=rule["check"],
        window=TimeWindow(
            start=time.fromisoformat(rule["window"]["start"]),
            end=time.fromisoformat(rule["window"]["end"]),
        ),
        min_consecutive_readings=rule["min_consecutive_readings"],
        severity=rule["severity"],
        column=rule.get("column"),
        operator=rule.get("operator"),
        threshold=_float_or_none(rule.get("threshold")),
        irradiance_gate=None
        if gate is None
        else IrradianceGate(
            enabled=gate["enabled"],
            column=gate["column"],
            operator=gate["operator"],
            threshold=float(gate["threshold"]),
        ),
    )


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)
