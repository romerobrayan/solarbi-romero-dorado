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
