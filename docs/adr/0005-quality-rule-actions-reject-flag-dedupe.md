# 0005. Quality rules declare an action: reject, flag or dedupe

- **Status:** Accepted
- **Date:** 2026-10-08
- **Phase:** 1

## Context

The assignment (Step 2) asks for at least three quality rules (physical range, duplicates,
missing data) and for the count of rows rejected by each rule. The simulator injects three
anomalies: negative power (`p_ac_kw = -1`), empty irradiance and fully duplicated rows.

Treating every failed rule as "drop the row" is simple but wrong for this data. A reading with
valid power and missing irradiance still describes the energy the inverter produced; dropping it
would understate the day's energy, the main KPI. On the other hand, a negative power value is a
sensor error and must not reach any sum.

Separately, a "plant fault" (zero power in daylight) is not a data-quality problem: the data may be
perfectly valid and show that the inverter stopped. Mixing both concepts would hide real faults
behind quality filters, or count sensor errors as plant failures.

## Decision

Each rule in `contracts/telemetria.yaml` declares an **action**:

| Action | Effect on the row | Used by |
|---|---|---|
| `reject` | Kept out of Silver | `range_p_ac_kw`, `missing_p_ac_kw` |
| `flag` | Loaded into Silver; the rule id is added to `silver.lectura_5min.dq_flags` | `missing_irradiancia`, `range_irradiancia`, `range_temp_modulo` |
| `dedupe` | Only one row per natural key `(dispositivo_id, ts)` survives (`keys.dedup.keep: first`) | `duplicate_key` |

- Every run writes one row per rule to `dq.rule_result` (`filas_afectadas`) and the totals to
  `dq.etl_run_log`. This is what the assignment's Step 2 reports.
- **Fault rules are a separate section** (`fault_rules`), evaluated on Silver, never on Bronze,
  and written to `dq.fault_event`. `p_ac_kw < 0` is a quality error (rejected); `p_ac_kw <= 0.01`
  between 09:00 and 15:00 local is a fault (`zero_power_daylight`); a missing daytime reading is
  its own fault rule (`missing_daytime_reading`).
- `dq_flags` is a `text[]` of rule ids rather than a bitmask: it is self-describing in SQL, Grafana
  and Power BI (`'missing_irradiancia' = ANY(dq_flags)`), and adding a rule to the contract cannot
  silently reassign bit positions. Storage cost is negligible next to the other columns.

There are two **percentages of valid data**, named differently on purpose:

- `dq.etl_run_log.pct_validas` (per run) = valid rows / rows read. Measures the quality of a
  delivered file and is what Step 2 asks for.
- `dwh.fact_energia_dia.pct_datos_validos` (per day) = unique valid readings / expected readings
  (86 400 / `frequency_seconds`). Measures completeness of the day; duplicates cannot inflate it,
  because the numerator counts unique timestamps and the denominator does not depend on the file.
  A day below `sla.pct_datos_validos_min` (95) gets `cumple_sla = false`.

## Consequences

- Energy stays complete when only secondary sensors fail; yield and Performance Ratio (later
  phases) exclude readings flagged on irradiance.
- Changing a rule's behavior is a contract change (a minor version), not a code change.
- The ETL needs a check (`range`, `not_null`, `unique`) implementation per kind, not per column;
  adding a rule of an existing kind needs no code.
- Analysts must know that flagged rows are in Silver; Grafana panels that need clean irradiance
  filter on `dq_flags`.
- A row can fail several `reject` rules; it is counted under each of them in `dq.rule_result`, so
  per-rule counts can add up to more than the rejected total (see the update below for how the
  report reconciles).

## Update — Phase 2 (2026-10-08): counting semantics and new rules

Implemented in `etl/sqlgen.py` (contract 1.1.0):

- **Order**: every rule is evaluated on every row; a row failing any `reject` rule is out. Dedupe
  runs **after** rejection, among the surviving rows, on the natural key with the timestamp already
  snapped to the grid, keeping the first by `source_row` (`keys.dedup.keep`). A valid copy therefore
  survives even when an earlier copy was rejected.
- **What each count means** (`dq.rule_result.filas_afectadas`):
  - `reject` rules: rows read that fail the rule (may overlap between rules);
  - `flag` rules: **valid** rows that keep the mark in `dq_flags` (a rejected row is not "flagged");
  - the `dedupe` rule: duplicates dropped.
- **Distinct totals** in `dq.etl_run_log` reconcile exactly, enforced by a CHECK constraint:
  `filas_leidas = filas_validas + filas_rechazadas + filas_deduplicadas`. `filas_marcadas` counts
  distinct valid rows with at least one flag.
- **New rules**: `missing_key` (empty `ts` or `dispositivo_id`), `invalid_format` (a value that does
  not parse as its type), `unknown_device` (not in the catalog) and `off_grid` (more than
  `grid.tolerance_seconds` from a 5-minute slot) reject; `snapped_to_grid` flags a reading moved to
  its slot, whose original timestamp is kept in `silver.lectura_5min.ts_origen`. Snapping plus
  dedupe guarantee at most 288 readings per local day, so `pct_datos_validos` never exceeds 100 %.
- The contract loader now requires every non-nullable column to be covered by a `not_null` rule
  with action `reject`, so a NULL can never abort the Silver load.
- `missing_daytime_reading` opens an event only after 3 consecutive missing slots (15 minutes):
  a single rejected reading is a data-quality issue, not a communication outage.
