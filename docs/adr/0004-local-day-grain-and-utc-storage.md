# 0004. Store timestamps in UTC; use the site's local day as the daily grain

- **Status:** Accepted
- **Date:** 2026-10-08
- **Phase:** 1

## Context

The simulator writes naive timestamps (`YYYY-MM-DD HH:MM:SS`) in Colombian local time: its sun
curve peaks at 12:00 local, and the assignment's fault window "09:00–15:00" is local time.
`America/Bogota` is UTC−5 with no daylight saving time. The real dataset (Phase 6) may come from
another time zone or already be in UTC.

Two different questions depend on time zones:

1. **Which instant does a reading belong to?** This must be unambiguous across sources and tools.
2. **Which day does a reading count toward?** `dwh.fact_energia_dia` has one row per day and
   device. If "day" meant the UTC day, the readings from 19:00 to 23:55 local would land on the next
   date: every local day's energy would be split across two `fecha_key` values, and the 288
   expected readings per day would never line up with a calendar day the operator recognizes.

## Decision

- **Storage:** `ts` is `timestamptz` everywhere after Bronze, so PostgreSQL stores UTC.
  The contract declares how to read the source: `source.source_timezone: America/Bogota`. The ETL
  localizes each naive timestamp with that zone, then converts it to UTC.
- **Daily grain:** `fecha_key` (YYYYMMDD) is the **local calendar day of the site**
  (`dwh.dim_sitio.zona_horaria`), never the UTC day. The ETL computes it as
  `(ts AT TIME ZONE <site time zone>)::date`.
- **Time windows in rules** (`fault_rules[].window`, e.g. 09:00–15:00) are local clock times of the
  source time zone and half-open: `start <= t < end`.
- Bronze keeps the original text unchanged; the conversion happens in the Bronze → Silver step.

## Consequences

- Grafana and Power BI can show any time zone correctly because the instant is unambiguous;
  Grafana renders in the browser's time zone by default (Colombia for our users).
- A local day always has exactly 86 400 / `frequency_seconds` slots (288), so
  `pct_datos_validos` (ADR 0005) compares like with like. This holds for `America/Bogota`, which has
  no DST. A site with DST would have 23- or 25-hour days; the ETL would then have to compute the
  expected readings per local day instead of using the constant. That is noted for Phase 6.
- Mixing sites in different time zones still works: each site's day comes from its own zone.
- Queries that group by day must use the local day (`AT TIME ZONE`) or join `dim_fecha` through
  the fact table. Grouping a hypertable by `time_bucket('1 day', ts)` alone would silently use UTC
  days; pass the time zone argument (`time_bucket('1 day', ts, 'America/Bogota')`).
