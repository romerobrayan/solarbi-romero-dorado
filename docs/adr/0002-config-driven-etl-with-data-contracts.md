# 0002. Drive the ETL from YAML data contracts

- **Status:** Accepted
- **Date:** 2026-10-08
- **Phase:** 0 (contracts are written in Phase 1)

## Context

Today the only source is a small simulator whose columns we control. In Phase 6 the professor will
deliver a real dataset of 4M+ rows whose schema we do not know yet: column names, units, timestamp
format, time zone and number of devices may all differ. If column names and quality thresholds are
hardcoded in Python, switching to the real data means rewriting the ETL under deadline pressure,
and the quality rules become invisible to the graders.

## Decision

Describe every source in a **data contract**: a YAML file in `contracts/`. The ETL code reads the
contract and works only with the **canonical** column names of the Silver layer (`dispositivo_id`,
`ts`, `p_ac_kw`, `irradiancia_wm2`, `temp_modulo_c`, …).

A contract declares, at least:

- the source: file pattern, delimiter, encoding, timestamp format and time zone;
- the mapping from source column names to canonical names, with types and units;
- the quality rules: required fields, valid ranges, duplicates on `(dispositivo_id, ts)`,
  expected 5-minute frequency and tolerance for gaps.

No module outside `contracts/` may reference a source column name. Rule results are written to
the `dq` schema so they can be audited and charted.

## Consequences

- Plugging in the real dataset becomes "write a new contract (and possibly a unit conversion)",
  not "rewrite the ETL".
- The quality rules are readable by non-programmers and versioned next to the code, which makes
  them easy to defend in the written report.
- The contract format itself must be validated (a malformed YAML must fail fast with a clear
  message); this needs tests from Phase 1 on.
- Generic code is slightly more abstract than a hardcoded script; keep the contract schema small
  and add fields only when a real need appears.
- The reader must not assume small data: process the source in chunks so the same code path works
  for 870 rows and for 4M rows.

## Update — Phase 1 (2026-10-08): the contract as built

`contracts/telemetria.yaml` (v1.0.0) is loaded by `etl/contract.py` in two validation layers:

1. **Structure**: `contracts/telemetria.schema.json` (JSON Schema 2020-12). Editors that support the
   `yaml-language-server` comment validate the file while typing.
2. **Semantics** in Python: references between sections (rule columns, natural key, device sites),
   `min < max`, IANA time zones, a frequency that divides a day, windows with start before end, and
   the supported major version. All problems are reported together in one `ContractError`.

Decisions taken while building it:

- **Versioning:** the ETL supports one major version (`SUPPORTED_MAJOR_VERSION = 1`) and refuses
  others; `check_source_header()` refuses a file whose header does not match
  `columns[].source_name` (missing, repeated or unannounced columns; `allow_extra_columns` can
  relax the last one).
- **Canonical vs. source names:** code uses only canonical Silver names. The minimum the pipeline
  needs (`ts`, `dispositivo_id`, `p_ac_kw`) must be declared. A nullable column may have
  `source_name: null`, meaning "this source does not provide it" (it lands as NULL).
- **Relative ranges:** `p_ac_kw` uses `max_nominal_factor: 1.1`, resolved against the device's
  `nominal_kwp` in the `devices` section, so one rule serves inverters of any size.
- **Identifiers as text:** `dispositivo_id` is a `string`. It is a label, never summed, and the
  real dataset may use serials such as `INV-001`.
- **Governance metadata** (owner, steward, custodian, change policy, SLA) lives in the same file, so
  the contract is also the governance document the report refers to.
- **Bronze landing table:** `bronze.telemetria_raw` has one text column per source column of this
  contract. A future source with different columns gets its own landing table through a migration
  (SQL, not Python).
