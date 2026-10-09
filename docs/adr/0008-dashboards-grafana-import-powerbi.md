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

**Two alerts on Silver, events as annotations.** The rule *Potencia cero en horario solar* queries
Silver as the assignment says. It looks at the **latest** reading of each device in the last 15
minutes: it is 1 when that reading has `p_ac_kw <= 0.01` and its local time is in [09:00, 15:00),
else 0. Pending period: 10 minutes (two more readings at zero), so one glitch does not page the
operator, and the alert resolves as soon as power returns. The threshold, window and time zone
come from the contract's `zero_power_daylight` rule; `tests/test_grafana.py` fails if they drift.

A rule over readings cannot see readings that never arrive. If the inverter or its gateway goes
down (the most common real fault), there are no rows: the zero-power rule gets *no data*, and it
must treat no data as OK because at night there are no rows on purpose (the first version had only
this rule, so a silent inverter notified nobody). The second rule, *Inversor sin datos en horario
solar* (`severity=warning`), therefore starts from the **device catalog**: `dwh.dim_dispositivo`
LEFT JOIN the readings of the last 15 minutes, one row per device whether or not it sent anything,
1 when the site's local time is in [09:00, 15:00) and the device has zero readings. Its window is
the contract's `missing_daytime_reading` outage (3 missing readings x 5 minutes = 900 s, no extra
pending period); window and severity are checked by the same drift test. It returns a row per
device even when all is well, so for it *no data* means an empty or unreadable catalog and is
reported as NoData, not hidden.

| Fault | Rows in Silver | Caught by |
|---|---|---|
| Inverter tripped (zero power) | yes, at 0 kW | *Potencia cero* (`noDataState: OK`) |
| Inverter or gateway silent | none | *Inversor sin datos* (catalog-driven) |
| Night | none | neither: both rules are limited to 09:00–15:00 |
`dq.fault_event` (written by the ETL) is shown as annotations and as a table: it is the historical
record, while the alert is the live signal.

**Notifications.** A webhook contact point delivers to a local echo service (`alert-receiver`,
compose profile `alerting`), so the payload is visible in its log. An email contact point shows how
production would notify the operator; it needs SMTP to send. Policy: `severity=critical` → webhook,
grouped by device, repeated every hour while the fault lasts; everything else, including the
`warning` of a silent device, goes to the default receiver (email).

**Dashboards as code with UI editing.** The provider allows UI edits while developing;
`scripts/export_grafana.py` writes the dashboard back to `grafana/dashboards/` normalized (sorted
keys, no `id`/`version`), so an unchanged dashboard produces no diff and the committed JSON stays the
source of truth (and is the "exported JSON" the assignment asks for).

**Replay for live evidence.** Alert rules evaluate a window ending "now", but the simulated data is
historical. `scripts/replay_live.py` simulates the IoT feed: it loads today's readings up to now,
then one small file per 5-minute slot through the normal ETL, with an inverter trip a few minutes
ahead. `--trip-in` shows Normal → Pending → Firing → Normal and a delivered webhook; `--gap-in`
stops sending for a while and shows the silent-device rule firing. Today's backfill starts after
the last reading already loaded, so a second replay does not overwrite the first one's readings.
It is a simulation; no real device exists.

**No future data.** The dashboard defaults to the last 7 days (`now-7d` to `now`), and every sample
is dated in the past (the fault sample covers Oct 2–4; the replay writes only up to the current
slot). A "real-time" screen must not need a range that ends in the future.

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
- The two rules split the cases cleanly: a trip never fires the silent-device rule (readings keep
  arriving, at zero) and a silent inverter never fires the zero-power rule (no rows). After the
  feed returns, the ETL records the hole as a `missing_daytime_reading` event (bounded by the last
  reading received, so a live day is not flagged ahead of time).
- Lesson for the report: an alert defined over the data cannot see the absence of data; monitoring
  needs an independent list of what *should* report (the catalog) to detect silence.
- The silent-device rule assumes near-real-time ingestion (here the replay's 5-minute
  micro-batches). With only the nightly batch ETL it would fire every day from 09:15: in production
  it needs the streaming or micro-batch feed it is designed for.
