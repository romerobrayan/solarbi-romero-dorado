"""SQL generated from the data contract: Bronze -> Silver -> Gold -> fault events.

The ETL is ELT: rows land in Bronze as text and every transformation runs as
set-based SQL inside PostgreSQL. This module writes that SQL from the contract.
Identifiers always go through sql.Identifier and contract values through
sql.Literal, so a column name or a threshold can never inject SQL.

Code here uses only canonical (Silver) column names; source names, units, ranges,
rules and thresholds come from the contract.
"""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from psycopg import sql

from etl.contract import DEVICE_COLUMN, Column, Contract, FaultRule, QualityRule

BRONZE_TABLE = sql.Identifier("bronze", "telemetria_raw")
SILVER_TABLE = sql.Identifier("silver", "lectura_5min")
PURGES = sql.Identifier("dq", "purga_log")  # day purges; also mark older Bronze rows superseded

# Temporary tables, dropped at commit.
STAGE = sql.Identifier("etl_resultado")  # one row per Bronze row, with every rule outcome
DAYS = sql.Identifier("etl_dias")  # (device, local day) pairs touched by the run
FAULTS = sql.Identifier("etl_fallas")  # fault events detected in those days

# Canonical Silver columns that Gold reads (Silver's schema, not the source's).
POWER_COLUMN = "p_ac_kw"
IRRADIANCE_COLUMN = "irradiancia_wm2"

SLOT = sql.Identifier("ts_slot")
LOADED_AT = sql.Identifier("bronze_loaded_at")
NOMINAL = sql.Identifier("dev__nominal_kwp")
REJECTED = sql.Identifier("rechazada")
RANK = sql.Identifier("rn")

SQL_OPERATORS = {"<": "<", "<=": "<=", ">": ">", ">=": ">=", "==": "="}
NON_FINITE = ("nan", "infinity", "+infinity", "-infinity", "inf", "+inf", "-inf")
# strftime tokens supported in source.timestamp_format, with their fixed width.
_STRFTIME_WIDTH = {"%Y": 4, "%m": 2, "%d": 2, "%H": 2, "%M": 2, "%S": 2}


# --- helpers ------------------------------------------------------------------------


def raw_column(name: str) -> sql.Identifier:
    """Trimmed source text of a canonical column (NULL when it is a null value)."""
    return sql.Identifier(f"raw__{name}")


def rule_column(rule_id: str) -> sql.Identifier:
    """Boolean column: the row fails the rule."""
    return sql.Identifier(f"rule__{rule_id}")


def timestamp_layout(fmt: str) -> tuple[str, dict[str, tuple[int, int]]]:
    """Anchored regex for a strftime format and the (1-based start, width) of each token.

    Every supported token has a fixed width, so fields are cut with substr() after a
    boolean match. Capture groups (regexp_match) are ~20x slower in PostgreSQL.
    """
    pattern, positions, offset = "^", {}, 1
    for token in re.split(r"(%.)", fmt):
        if not token:
            continue
        if token.startswith("%"):
            width = _STRFTIME_WIDTH.get(token)
            if width is None:
                raise ValueError(
                    f"timestamp_format token {token!r} is not supported "
                    f"(supported: {', '.join(_STRFTIME_WIDTH)})"
                )
            if token in positions:
                raise ValueError(f"timestamp_format repeats {token!r}")
            positions[token] = (offset, width)
            pattern += f"[0-9]{{{width}}}"
            offset += width
        else:
            pattern += re.escape(token)
            offset += len(token)
    missing = [t for t in ("%Y", "%m", "%d", "%H", "%M") if t not in positions]
    if missing:
        raise ValueError(f"timestamp_format lacks {', '.join(missing)}")
    return pattern + "$", positions


def _or(parts: list[sql.Composable]) -> sql.Composable:
    return sql.SQL(" OR ").join(parts) if parts else sql.SQL("false")


def _step(seconds: float) -> sql.Composed:
    return sql.SQL("make_interval(secs => {})").format(sql.Literal(seconds))


def _text_array(values: list[str]) -> sql.Composed:
    if not values:
        return sql.SQL("'{}'::text[]")
    return sql.SQL("ARRAY[{}]::text[]").format(sql.SQL(", ").join(map(sql.Literal, values)))


def _check_rules(contract: Contract) -> list[QualityRule]:
    """Rules evaluated row by row (dedupe works on the ranking instead)."""
    return [rule for rule in contract.quality_rules if rule.check != "unique"]


def _flag_rules(contract: Contract) -> list[QualityRule]:
    return [rule for rule in _check_rules(contract) if rule.action == "flag"]


def _reject_rules(contract: Contract) -> list[QualityRule]:
    return [rule for rule in _check_rules(contract) if rule.action == "reject"]


# --- Bronze -> Silver -----------------------------------------------------------------


def _raw_expression(contract: Contract, column: Column) -> sql.Composable:
    if column.source_name is None:
        return sql.SQL("NULL::text")
    source = sql.SQL("b.{}").format(sql.Identifier(column.source_name))
    return sql.SQL(
        "CASE WHEN {src} IS NULL OR btrim({src}) = ANY({nulls}) THEN NULL ELSE btrim({src}) END"
    ).format(src=source, nulls=_text_array(list(contract.source.null_values)))


def _parse_expression(contract: Contract, column: Column) -> sql.Composable:
    raw = raw_column(column.name)
    if column.type == "string":
        return raw
    if column.type == "float":
        return sql.SQL(
            "CASE WHEN pg_input_is_valid({raw}, 'double precision') "
            "AND lower({raw}) <> ALL({non_finite}) THEN {raw}::double precision END"
        ).format(raw=raw, non_finite=_text_array(list(NON_FINITE)))
    if column.type == "integer":
        return sql.SQL(
            "CASE WHEN pg_input_is_valid({raw}, 'bigint') THEN {raw}::bigint END"
        ).format(raw=raw)
    # timestamp: check the shape, cut the fields, rebuild ISO text, validate, localize.
    pattern, positions = timestamp_layout(contract.source.timestamp_format)

    def field(token: str) -> sql.Composable:
        if token not in positions:
            return sql.Literal("00")
        start, width = positions[token]
        return sql.SQL("substr({}, {}, {})").format(raw, sql.Literal(start), sql.Literal(width))

    iso = sql.SQL("({} || '-' || {} || '-' || {} || ' ' || {} || ':' || {} || ':' || {})").format(
        *(field(t) for t in ("%Y", "%m", "%d", "%H", "%M", "%S"))
    )
    return sql.SQL(
        "CASE WHEN {raw} ~ {pattern} AND pg_input_is_valid({iso}, 'timestamp') "
        "THEN {iso}::timestamp AT TIME ZONE {tz} END"
    ).format(raw=raw, pattern=sql.Literal(pattern), iso=iso, tz=sql.Literal(contract.timezone.key))


def _range_failure(contract: Contract, column: Column) -> list[sql.Composable]:
    value = sql.Identifier(column.name)
    value_range = column.range
    if value_range is None:
        return []
    bounds = []
    if value_range.min is not None:
        bounds.append(sql.SQL("{} < {}").format(value, sql.Literal(value_range.min)))
    if value_range.max is not None:
        bounds.append(sql.SQL("{} > {}").format(value, sql.Literal(value_range.max)))
    if value_range.max_nominal_factor is not None:
        bounds.append(
            sql.SQL("{} > {} * {}").format(
                value, sql.Literal(value_range.max_nominal_factor), NOMINAL
            )
        )
    return [sql.SQL("({})").format(_or(bounds))]


def rule_failure(contract: Contract, rule: QualityRule) -> sql.Composable:
    """Boolean SQL expression: the row fails the rule (NULL counts as passing)."""
    columns = [contract.column(name) for name in rule.columns]
    if rule.check == "parseable":
        parts = [
            sql.SQL("({} IS NOT NULL AND {} IS NULL)").format(
                raw_column(c.name), sql.Identifier(c.name)
            )
            for c in columns
        ]
    elif rule.check == "not_null":
        parts = [sql.SQL("{} IS NULL").format(raw_column(c.name)) for c in columns]
    elif rule.check == "range":
        parts = [part for c in columns for part in _range_failure(contract, c)]
    elif rule.check == "known_device":
        parts = [
            sql.SQL("({} IS NOT NULL AND {} IS NULL)").format(
                sql.Identifier(DEVICE_COLUMN), NOMINAL
            )
        ]
    elif rule.check == "grid":
        parts = [
            sql.SQL("abs(extract(epoch FROM {} - {})) > {}").format(
                sql.Identifier(contract.grid.column),
                SLOT,
                sql.Literal(contract.grid.tolerance_seconds),
            )
        ]
    elif rule.check == "on_grid":
        parts = [sql.SQL("{} <> {}").format(sql.Identifier(contract.grid.column), SLOT)]
    else:
        raise ValueError(f"rule {rule.id}: check {rule.check!r} is not evaluated row by row")
    return sql.SQL("COALESCE({}, false)").format(_or(parts))


def _slot_expression(contract: Contract, timestamp: sql.Composable) -> sql.Composed:
    """The 5-minute slot of a timestamp: the nearest grid point, in the source's time zone."""
    frequency = contract.frequency_seconds
    return sql.SQL("date_bin({step}, {ts} + {half_step}, {origin})").format(
        step=_step(frequency),
        ts=timestamp,
        half_step=_step(frequency / 2),
        origin=sql.SQL("(TIMESTAMP '2000-01-01 00:00:00' AT TIME ZONE {})").format(
            sql.Literal(contract.timezone.key)
        ),
    )


def _not_superseded(slot: sql.Composable, loaded_at: sql.Composable) -> sql.Composed:
    """The Bronze row is current: no later purge covers its slot (see dq.purga_log)."""
    return sql.SQL(
        """NOT EXISTS (
        SELECT 1 FROM {purges} AS p
        WHERE {slot} >= p.ts_desde AND {slot} < p.ts_hasta AND {loaded_at} < p.ejecutada_en
    )"""
    ).format(purges=PURGES, slot=slot, loaded_at=loaded_at)


def stage_sql(contract: Contract, bronze_run_id: UUID) -> sql.Composed:
    """Temp table with every current Bronze row of a run, parsed, checked, ranked for dedupe.

    The CTEs are MATERIALIZED on purpose: inlined, every reference to a parsed
    column would re-run its parse expression (regexes, input validation) for
    each rule that reads it.

    bronze_run_id is the run whose Bronze rows are transformed. Rows superseded by a
    day purge (dq.purga_log) are left out, so re-running an old file cannot bring a
    purged day back.
    """
    grid_column = sql.Identifier(contract.grid.column)
    raw_select = sql.SQL(",\n           ").join(
        sql.SQL("{} AS {}").format(_raw_expression(contract, c), raw_column(c.name))
        for c in contract.columns
    )
    parsed_select = sql.SQL(",\n           ").join(
        sql.SQL("{} AS {}").format(_parse_expression(contract, c), sql.Identifier(c.name))
        for c in contract.columns
    )
    devices = sql.SQL(", ").join(
        sql.SQL("({}, {})").format(sql.Literal(d.dispositivo_id), sql.Literal(d.nominal_kwp))
        for d in contract.devices
    )
    checks = sql.SQL(",\n           ").join(
        sql.SQL("{} AS {}").format(rule_failure(contract, rule), rule_column(rule.id))
        for rule in _check_rules(contract)
    )
    rejected = _or([rule_column(rule.id) for rule in _reject_rules(contract)])
    partition = sql.SQL(", ").join(
        SLOT if name == contract.grid.column else sql.Identifier(name)
        for name in contract.natural_key
    )
    order = sql.SQL("ASC" if contract.dedup_keep == "first" else "DESC")
    return sql.SQL(
        """CREATE TEMP TABLE {stage} ON COMMIT DROP AS
WITH raw AS (
    SELECT b.source_row, b.loaded_at AS {loaded_at},
           {raw_select}
    FROM {bronze} AS b
    WHERE b.run_id = {bronze_run_id}
), parsed AS MATERIALIZED (
    SELECT raw.*,
           {parsed_select}
    FROM raw
), keyed AS MATERIALIZED (
    SELECT parsed.*,
           dev.nominal_kwp AS {nominal},
           {slot_expression} AS {slot}
    FROM parsed
    LEFT JOIN (VALUES {devices}) AS dev (device_id, nominal_kwp)
           ON dev.device_id = parsed.{device}
), vigente AS (
    SELECT keyed.*
    FROM keyed
    WHERE {not_superseded}
), checked AS MATERIALIZED (
    SELECT vigente.*,
           {checks}
    FROM vigente
), decided AS (
    SELECT checked.*, ({rejected}) AS {rejected_column}
    FROM checked
)
SELECT decided.*,
       row_number() OVER (PARTITION BY {rejected_column}, {partition}
                          ORDER BY decided.source_row {order}) AS {rank}
FROM decided"""
    ).format(
        stage=STAGE,
        loaded_at=LOADED_AT,
        raw_select=raw_select,
        bronze=BRONZE_TABLE,
        bronze_run_id=sql.Literal(bronze_run_id),
        parsed_select=parsed_select,
        nominal=NOMINAL,
        slot_expression=_slot_expression(contract, sql.SQL("parsed.{}").format(grid_column)),
        not_superseded=_not_superseded(
            sql.SQL("keyed.{}").format(SLOT), sql.SQL("keyed.{}").format(LOADED_AT)
        ),
        slot=SLOT,
        devices=devices,
        device=sql.Identifier(DEVICE_COLUMN),
        checks=checks,
        rejected=rejected,
        rejected_column=REJECTED,
        partition=partition,
        order=order,
        rank=RANK,
    )


VALID = sql.SQL("NOT {} AND {} = 1").format(REJECTED, RANK)


def counts_sql(contract: Contract) -> sql.Composed:
    """Totals of the run and rows affected per rule.

    reject rules count over all rows read (they may overlap); flag rules count over
    the valid rows that keep the mark; the dedupe rule counts the dropped duplicates.
    """
    flagged = _or([rule_column(rule.id) for rule in _flag_rules(contract)])
    per_rule = []
    for rule in contract.quality_rules:
        if rule.action == "reject":
            condition = rule_column(rule.id)
        elif rule.action == "flag":
            condition = sql.SQL("{} AND {}").format(VALID, rule_column(rule.id))
        else:
            condition = sql.SQL("NOT {} AND {} > 1").format(REJECTED, RANK)
        per_rule.append(
            sql.SQL("count(*) FILTER (WHERE {}) AS {}").format(condition, rule_column(rule.id))
        )
    return sql.SQL(
        """SELECT count(*) AS leidas,
       count(*) FILTER (WHERE {rejected}) AS rechazadas,
       count(*) FILTER (WHERE NOT {rejected} AND {rank} > 1) AS deduplicadas,
       count(*) FILTER (WHERE {valid}) AS validas,
       count(*) FILTER (WHERE {valid} AND ({flagged})) AS marcadas,
       {per_rule}
FROM {stage}"""
    ).format(
        rejected=REJECTED,
        rank=RANK,
        valid=VALID,
        flagged=flagged,
        per_rule=sql.SQL(",\n       ").join(per_rule),
        stage=STAGE,
    )


def silver_upsert_sql(contract: Contract, run_id: UUID) -> sql.Composed:
    """UPSERT the valid rows into silver.lectura_5min, stamped with run_id."""
    names = [c.name for c in contract.columns]
    target = sql.SQL(", ").join(
        [sql.Identifier(n) for n in names]
        + [sql.Identifier("dq_flags"), sql.Identifier("ts_origen"), sql.Identifier("run_id")]
    )
    values = [
        sql.SQL("r.{}").format(SLOT if n == contract.grid.column else sql.Identifier(n))
        for n in names
    ]
    flags = _flag_rules(contract)
    if flags:
        flag_array = sql.SQL("array_remove(ARRAY[{}]::text[], NULL)").format(
            sql.SQL(", ").join(
                sql.SQL("CASE WHEN r.{} THEN {} END").format(rule_column(r.id), sql.Literal(r.id))
                for r in flags
            )
        )
    else:
        flag_array = sql.SQL("'{}'::text[]")
    grid_column = sql.Identifier(contract.grid.column)
    original_ts = sql.SQL("CASE WHEN r.{grid} <> r.{slot} THEN r.{grid} END").format(
        grid=grid_column, slot=SLOT
    )
    updates = [
        sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(n))
        for n in names + ["dq_flags", "ts_origen", "run_id"]
        if n not in contract.natural_key
    ]
    return sql.SQL(
        """INSERT INTO {silver} AS t ({target})
SELECT {values}, {flags}, {original_ts}, {run_id}
FROM {stage} AS r
WHERE NOT r.{rejected} AND r.{rank} = 1
ON CONFLICT ({key}) DO UPDATE SET
    {updates}"""
    ).format(
        silver=SILVER_TABLE,
        target=target,
        values=sql.SQL(", ").join(values),
        flags=flag_array,
        original_ts=original_ts,
        run_id=sql.Literal(run_id),
        stage=STAGE,
        rejected=REJECTED,
        rank=RANK,
        key=sql.SQL(", ").join(map(sql.Identifier, contract.natural_key)),
        updates=sql.SQL(",\n    ").join(updates),
    )


# --- dimensions --------------------------------------------------------------------------


def dimension_sql(contract: Contract) -> list[sql.Composed]:
    """Upsert dim_sitio and dim_dispositivo from the contract's catalog."""
    sites = sql.SQL(", ").join(
        sql.SQL("({}, {}, {}, {})").format(
            sql.Literal(s.sitio_id),
            sql.Literal(s.nombre),
            sql.Literal(s.ciudad),
            sql.Literal(s.timezone.key),
        )
        for s in contract.sites
    )
    devices = sql.SQL(", ").join(
        sql.SQL("({}, {}, {}, {}::numeric, {})").format(
            sql.Literal(d.dispositivo_id),
            sql.Literal(d.nombre),
            sql.Literal(d.tipo),
            sql.Literal(d.nominal_kwp),
            sql.Literal(d.sitio_id),
        )
        for d in contract.devices
    )
    return [
        sql.SQL(
            """INSERT INTO dwh.dim_sitio (sitio_id, nombre, ciudad, zona_horaria)
VALUES {sites}
ON CONFLICT (sitio_id) DO UPDATE SET
    nombre = EXCLUDED.nombre, ciudad = EXCLUDED.ciudad, zona_horaria = EXCLUDED.zona_horaria"""
        ).format(sites=sites),
        sql.SQL(
            """INSERT INTO dwh.dim_dispositivo
    (dispositivo_id, nombre, tipo, nominal_kwp, sitio_key)
SELECT v.dispositivo_id, v.nombre, v.tipo, v.nominal_kwp, s.sitio_key
FROM (VALUES {devices}) AS v (dispositivo_id, nombre, tipo, nominal_kwp, sitio_id)
JOIN dwh.dim_sitio AS s ON s.sitio_id = v.sitio_id
ON CONFLICT (dispositivo_id) DO UPDATE SET
    nombre = EXCLUDED.nombre, tipo = EXCLUDED.tipo,
    nominal_kwp = EXCLUDED.nominal_kwp, sitio_key = EXCLUDED.sitio_key"""
        ).format(devices=devices),
    ]


# --- Gold ----------------------------------------------------------------------------------


def days_sql() -> sql.Composed:
    """Temp table of the (device, LOCAL day) pairs that received valid rows in this run."""
    return sql.SQL(
        """CREATE TEMP TABLE {days} ON COMMIT DROP AS
SELECT x.*,
       (x.fecha::timestamp AT TIME ZONE x.zona) AS dia_inicio,
       ((x.fecha + 1)::timestamp AT TIME ZONE x.zona) AS dia_fin
FROM (
    SELECT DISTINCT d.dispositivo_id, d.dispositivo_key, s.zona_horaria AS zona,
           (r.{slot} AT TIME ZONE s.zona_horaria)::date AS fecha
    FROM {stage} AS r
    JOIN dwh.dim_dispositivo AS d ON d.dispositivo_id = r.{device}
    JOIN dwh.dim_sitio AS s ON s.sitio_key = d.sitio_key
    WHERE NOT r.{rejected} AND r.{rank} = 1
) AS x"""
    ).format(
        days=DAYS,
        slot=SLOT,
        stage=STAGE,
        device=sql.Identifier(DEVICE_COLUMN),
        rejected=REJECTED,
        rank=RANK,
    )


def ensure_dates_sql() -> sql.Composed:
    return sql.SQL(
        "SELECT dwh.ensure_dim_fecha(min(fecha), max(fecha)) FROM {} HAVING count(*) > 0"
    ).format(DAYS)


def gold_upsert_sql(contract: Contract, run_id: UUID) -> sql.Composed:
    """Recompute dwh.fact_energia_dia for every touched (local day, device) from Silver.

    All of Silver is aggregated for those days (not only this file's rows), so a day
    fed by several files stays correct. Rows are stamped with run_id. Returns one row:
    (rows upserted, total energy of those rows).
    """
    frequency = contract.frequency_seconds
    expected = contract.expected_readings_per_day
    irradiance_flags = [
        rule.id for rule in _flag_rules(contract) if IRRADIANCE_COLUMN in rule.columns
    ]
    has_irradiance = IRRADIANCE_COLUMN in contract.column_names
    irradiance_sum = (
        sql.SQL("sum(l.{irr}) FILTER (WHERE NOT (l.dq_flags && {flags}))").format(
            irr=sql.Identifier(IRRADIANCE_COLUMN), flags=_text_array(irradiance_flags)
        )
        if has_irradiance
        else sql.SQL("NULL::double precision")
    )
    return sql.SQL(
        """WITH agg AS (
    SELECT dd.fecha, dd.dispositivo_key,
           count(*) AS lecturas,
           sum(l.{power}) AS suma_potencia,
           max(l.{power}) AS potencia_max,
           {irradiance_sum} AS suma_irradiancia
    FROM {days} AS dd
    JOIN {silver} AS l
      ON l.{device} = dd.dispositivo_id AND l.ts >= dd.dia_inicio AND l.ts < dd.dia_fin
    GROUP BY dd.fecha, dd.dispositivo_key
), upserted AS (
    INSERT INTO dwh.fact_energia_dia AS f (
        fecha_key, dispositivo_key, energia_kwh, lecturas_validas, lecturas_esperadas,
        cumple_sla, p_max_kw, irradiacion_kwh_m2, run_id, updated_at
    )
    SELECT to_char(a.fecha, 'YYYYMMDD')::integer,
           a.dispositivo_key,
           -- energy of a reading = power x frequency_seconds / 3600 (5/60 h for 300 s)
           round((a.suma_potencia * {frequency} / 3600.0)::numeric, 4),
           a.lecturas,
           {expected},
           round(100.0 * a.lecturas / {expected}, 2) >= {sla_min},
           round(a.potencia_max::numeric, 3),
           round((a.suma_irradiancia * {frequency} / 3600.0 / 1000.0)::numeric, 4),
           {run_id},
           now()
    FROM agg AS a
    ON CONFLICT (fecha_key, dispositivo_key) DO UPDATE SET
        energia_kwh = EXCLUDED.energia_kwh,
        lecturas_validas = EXCLUDED.lecturas_validas,
        lecturas_esperadas = EXCLUDED.lecturas_esperadas,
        cumple_sla = EXCLUDED.cumple_sla,
        p_max_kw = EXCLUDED.p_max_kw,
        irradiacion_kwh_m2 = EXCLUDED.irradiacion_kwh_m2,
        run_id = EXCLUDED.run_id,
        updated_at = EXCLUDED.updated_at
    RETURNING f.energia_kwh
)
SELECT count(*), COALESCE(sum(energia_kwh), 0) FROM upserted"""
    ).format(
        power=sql.Identifier(POWER_COLUMN),
        irradiance_sum=irradiance_sum,
        days=DAYS,
        silver=SILVER_TABLE,
        device=sql.Identifier(DEVICE_COLUMN),
        frequency=sql.Literal(frequency),
        expected=sql.Literal(expected),
        sla_min=sql.Literal(contract.sla.pct_datos_validos_min),
        run_id=sql.Literal(run_id),
    )


# --- fault events ----------------------------------------------------------------------------


def _islands(contract: Contract, rule: FaultRule, readings: sql.Composable) -> sql.Composed:
    """Group consecutive slots of `readings` (dispositivo_id, ts) into events."""
    frequency = contract.frequency_seconds
    return sql.SQL(
        """SELECT i.dispositivo_id, {rule_id} AS rule_id, {severity} AS severity,
       min(i.ts) AS ts_inicio, max(i.ts) + {step} AS ts_fin, count(*)::integer AS lecturas
FROM (
    SELECT e.dispositivo_id, e.ts,
           floor(extract(epoch FROM e.ts) / {frequency})::bigint
               - row_number() OVER (PARTITION BY e.dispositivo_id ORDER BY e.ts) AS grupo
    FROM ({readings}) AS e
) AS i
GROUP BY i.dispositivo_id, i.grupo
HAVING count(*) >= {min_readings}"""
    ).format(
        rule_id=sql.Literal(rule.id),
        severity=sql.Literal(rule.severity),
        step=_step(frequency),
        frequency=sql.Literal(frequency),
        readings=readings,
        min_readings=sql.Literal(rule.min_consecutive_readings),
    )


def fault_rule_sql(contract: Contract, rule: FaultRule) -> sql.Composed:
    """Events of one fault rule in the touched days. Windows are local clock times of the site."""
    window = sql.SQL(
        "(l.ts AT TIME ZONE dd.zona)::time >= {} AND (l.ts AT TIME ZONE dd.zona)::time < {}"
    ).format(sql.Literal(rule.window.start), sql.Literal(rule.window.end))
    if rule.check == "threshold_in_window":
        conditions = [
            window,
            sql.SQL("l.{} {} {}").format(
                sql.Identifier(rule.column),
                sql.SQL(SQL_OPERATORS[rule.operator]),
                sql.Literal(rule.threshold),
            ),
        ]
        gate = rule.irradiance_gate
        if gate is not None and gate.enabled:
            conditions.append(
                sql.SQL("l.{} {} {}").format(
                    sql.Identifier(gate.column),
                    sql.SQL(SQL_OPERATORS[gate.operator]),
                    sql.Literal(gate.threshold),
                )
            )
        readings = sql.SQL(
            """SELECT l.{device} AS dispositivo_id, l.ts
    FROM {days} AS dd
    JOIN {silver} AS l
      ON l.{device} = dd.dispositivo_id AND l.ts >= dd.dia_inicio AND l.ts < dd.dia_fin
    WHERE {conditions}"""
        ).format(
            device=sql.Identifier(DEVICE_COLUMN),
            days=DAYS,
            silver=SILVER_TABLE,
            conditions=sql.SQL(" AND ").join(conditions),
        )
    elif rule.check == "missing_in_window":
        # A slot can only be missing if a later reading of that day already arrived:
        # data after the last reading has not been sent yet (live feed, partial file).
        # Bounded by data, not by the clock, so re-running a load stays deterministic.
        readings = sql.SQL(
            """SELECT dd.dispositivo_id, g.ts
    FROM {days} AS dd
    CROSS JOIN LATERAL (
        SELECT max(l.ts) AS ultima FROM {silver} AS l
        WHERE l.{device} = dd.dispositivo_id AND l.ts >= dd.dia_inicio AND l.ts < dd.dia_fin
    ) AS u
    CROSS JOIN LATERAL generate_series(
        (dd.fecha + {start})::timestamp AT TIME ZONE dd.zona,
        (dd.fecha + {end})::timestamp AT TIME ZONE dd.zona - {step},
        {step}
    ) AS g (ts)
    WHERE g.ts < u.ultima
      AND NOT EXISTS (
        SELECT 1 FROM {silver} AS l WHERE l.{device} = dd.dispositivo_id AND l.ts = g.ts
    )"""
        ).format(
            days=DAYS,
            start=sql.Literal(rule.window.start),
            end=sql.Literal(rule.window.end),
            step=_step(contract.frequency_seconds),
            silver=SILVER_TABLE,
            device=sql.Identifier(DEVICE_COLUMN),
        )
    else:
        raise ValueError(f"fault rule {rule.id}: unknown check {rule.check!r}")
    return _islands(contract, rule, readings)


def faults_sql(contract: Contract, run_id: UUID) -> list[sql.Composed]:
    """Detect events for every fault rule, upsert them and drop stale ones in the touched days."""
    if not contract.fault_rules:
        return []
    detect = sql.SQL("\nUNION ALL\n").join(
        fault_rule_sql(contract, rule) for rule in contract.fault_rules
    )
    rule_ids = _text_array([rule.id for rule in contract.fault_rules])
    return [
        sql.SQL("CREATE TEMP TABLE {} ON COMMIT DROP AS\n{}").format(FAULTS, detect),
        sql.SQL(
            """INSERT INTO dq.fault_event AS f
    (dispositivo_id, rule_id, severity, ts_inicio, ts_fin, lecturas, run_id)
SELECT dispositivo_id, rule_id, severity, ts_inicio, ts_fin, lecturas, {run_id}
FROM {faults}
ON CONFLICT (dispositivo_id, rule_id, ts_inicio) DO UPDATE SET
    severity = EXCLUDED.severity, ts_fin = EXCLUDED.ts_fin,
    lecturas = EXCLUDED.lecturas, run_id = EXCLUDED.run_id"""
        ).format(faults=FAULTS, run_id=sql.Literal(run_id)),
        sql.SQL(
            """DELETE FROM dq.fault_event AS f
USING {days} AS dd
WHERE f.dispositivo_id = dd.dispositivo_id
  AND f.ts_inicio >= dd.dia_inicio AND f.ts_inicio < dd.dia_fin
  AND f.rule_id = ANY({rule_ids})
  AND NOT EXISTS (
      SELECT 1 FROM {faults} AS n
      WHERE n.dispositivo_id = f.dispositivo_id AND n.rule_id = f.rule_id
        AND n.ts_inicio = f.ts_inicio
  )"""
        ).format(days=DAYS, rule_ids=rule_ids, faults=FAULTS),
    ]


# --- day purge (scripts/purge_days.py) -------------------------------------------------------


def superseded_bronze_sql(
    contract: Contract, ts_desde: datetime, ts_hasta: datetime
) -> sql.Composed:
    """Count the current Bronze rows whose slot falls in [ts_desde, ts_hasta): the rows that a
    purge of those days supersedes (Bronze is append-only, so they are marked, not deleted)."""
    column = contract.column(contract.grid.column)
    return sql.SQL(
        """WITH raw AS (
    SELECT b.loaded_at, {raw} AS {raw_name}
    FROM {bronze} AS b
), parsed AS MATERIALIZED (
    SELECT raw.loaded_at, {parse} AS {name}
    FROM raw
), keyed AS (
    SELECT {slot_expression} AS {slot}, parsed.loaded_at AS {loaded_at}
    FROM parsed
)
SELECT count(*) FROM keyed
WHERE keyed.{slot} >= {ts_desde} AND keyed.{slot} < {ts_hasta}
  AND {not_superseded}"""
    ).format(
        raw=_raw_expression(contract, column),
        raw_name=raw_column(column.name),
        bronze=BRONZE_TABLE,
        parse=_parse_expression(contract, column),
        name=sql.Identifier(column.name),
        slot_expression=_slot_expression(
            contract, sql.SQL("parsed.{}").format(sql.Identifier(column.name))
        ),
        slot=SLOT,
        loaded_at=LOADED_AT,
        ts_desde=sql.Literal(ts_desde),
        ts_hasta=sql.Literal(ts_hasta),
        not_superseded=_not_superseded(
            sql.SQL("keyed.{}").format(SLOT), sql.SQL("keyed.{}").format(LOADED_AT)
        ),
    )
