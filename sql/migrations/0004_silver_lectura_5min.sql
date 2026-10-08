-- Silver: one validated reading per device and 5-minute interval.
-- ts is stored in UTC; the source's local time zone comes from the contract
-- (source.source_timezone). Rules with action 'reject' or 'dedupe' keep rows out
-- of this table; rules with action 'flag' keep the row and record their id in dq_flags.

CREATE TABLE silver.lectura_5min (
    ts              timestamptz      NOT NULL,  -- start of the interval, UTC
    dispositivo_id  text             NOT NULL,
    p_ac_kw         double precision NOT NULL,  -- kW
    irradiancia_wm2 double precision,           -- W/m2
    temp_modulo_c   double precision,           -- degrees C
    dq_flags        text[]           NOT NULL DEFAULT '{}',  -- ids of failed 'flag' rules
    run_id          uuid             NOT NULL   -- dq.etl_run_log.run_id of the last write
);

COMMENT ON TABLE silver.lectura_5min IS
    'Validated 5-minute readings, one per (dispositivo_id, ts). TimescaleDB hypertable.';

-- Hypertable partitioned by time in 7-day chunks (sizing in docs/adr/0001).
SELECT create_hypertable('silver.lectura_5min', by_range('ts', INTERVAL '7 days'));

-- Natural key from the contract (keys.natural_key). Also serves "latest readings
-- of one device" queries, so no separate (dispositivo_id, ts DESC) index is needed.
CREATE UNIQUE INDEX lectura_5min_dispositivo_ts_uk
    ON silver.lectura_5min (dispositivo_id, ts DESC);
