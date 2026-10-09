# 0008. Grafana reads Silver and Gold, Power BI imports Gold; the alert runs on Silver

- **Status:** Accepted
- **Date:** 2026-10-08
- **Phase:** 3

## Context

The assignment asks for the same data in two tools: Power BI (card with total energy, line chart
of daily energy, card with % valid data, all from DAX measures) and Grafana (5-minute power time
series with `$__timeFilter`, a threshold or alert for zero power between 09:00 and 15:00, and a
stat panel with the energy of the selected day), with the Grafana dashboard exported as JSON.
"One governed dataset, two views": both must agree on every shared number.

## Decision

**Who reads what**

| Consumer | Layer | Why |
|---|---|---|
| Grafana, power panels and alert | Silver (`silver.lectura_5min`) | The operator needs every 5-minute reading, minutes after it arrives |
| Grafana, "energy of the day" stat | Gold (`dwh.fact_energia_dia`) | The day's energy must be the governed number, identical to Power BI |
| Grafana, quality and faults | `dq.etl_run_log`, `dq.rule_result`, `dq.fault_event` | Data quality and detected events are operational information |
| Power BI | Gold only (fact + 3 dimensions) | Management decisions on the star schema; no raw readings |

Both connect with read-only roles (`grafana_reader`, `powerbi_reader`).

**Power BI in Import mode.** Gold changes once a day (the nightly ETL), the model is tiny, and
Import gives the fastest visuals and full DAX. DirectQuery would only pay off for near-real-time
data or tables too large to import; near-real-time is Grafana's job here. Measures live in a
`_Medidas` table written in TMDL by `scripts/powerbi_model.py`, so they are reviewed as code. `%
datos válidos` is a ratio of sums (`SUM(validas) / SUM(esperadas)`), never an average of daily
percentages, and the Grafana stat uses the same definition.

**Alert on Silver, events as annotations.** The rule *Potencia cero en horario solar* queries
Silver as the assignment says. It looks at the **latest** reading of each device in the last 15
minutes: it is 1 when that reading has `p_ac_kw <= 0.01` and its local time is in [09:00, 15:00),
else 0. Pending period: 10 minutes (two more readings at zero), so one glitch does not page the
operator, and the alert resolves as soon as power returns. The threshold, window and time zone
come from the contract's `zero_power_daylight` rule; `tests/test_grafana.py` fails if they drift.
`dq.fault_event` (written by the ETL) is shown as annotations and as a table: it is the historical
record, while the alert is the live signal.

**Notifications.** A webhook contact point delivers to a local echo service (`alert-receiver`,
compose profile `alerting`), so the payload is visible in its log. An email contact point shows how
production would notify the operator; it needs SMTP to send. Policy: `severity=critical` → webhook,
grouped by device, repeated every hour while the fault lasts.

**Dashboards as code with UI editing.** The provider allows UI edits while developing;
`scripts/export_grafana.py` writes the dashboard back to `grafana/dashboards/` normalized (sorted
keys, no `id`/`version`), so an unchanged dashboard produces no diff and the committed JSON stays the
source of truth (and is the "exported JSON" the assignment asks for).

**Replay for live evidence.** Alert rules evaluate a window ending "now", but the simulated data is
historical. `scripts/replay_live.py` simulates the IoT feed: it loads today's readings up to now,
then one small file per 5-minute slot through the normal ETL, with an inverter trip a few minutes
ahead. This shows Normal → Pending → Firing → Normal and a delivered webhook. It is a simulation;
no real device exists.

## Consequences

- Grafana's "energy of the day" and Power BI's card agree by construction (same Gold row, same
  ratio-of-sums definition); verified in `docs/evidencias/fase-3.md`.
- The alert can only fire with readings stamped between 09:00 and 15:00 local: the live demo has to
  run in those hours. At night the replay loads data and the rule correctly stays Normal.
- Alerting provisioning gotcha: Grafana expands `${VAR}` in contact points but does **not** process
  rule files, so `$__timeFilter` and `{{ $labels.x }}` are written literally there (an escaped
  `$$` was stored as-is and broke the query).
- The Power BI project needs one manual pass in Desktop (connect, save as `.pbip`, build the page);
  everything else is text in the repo. Data are not versioned (`cache.abf` is ignored).
- Silent data loss (no readings at all) is not this alert's job: it is the `missing_daytime_reading`
  fault rule, bounded by the last reading received so that a live day is not flagged ahead of time.
