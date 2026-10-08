-- Data-quality audit: one row per ETL run, one row per quality rule and run, and
-- the fault events the ETL detects on Silver with the contract's fault_rules.
-- Grafana reads these tables in Phase 3.

CREATE TABLE dq.etl_run_log (
    run_id           uuid          PRIMARY KEY,
    started_at       timestamptz   NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    status           text          NOT NULL DEFAULT 'running'
                                   CHECK (status IN ('running', 'succeeded', 'failed')),
    contract_version text          NOT NULL,
    source_file      text          NOT NULL,
    source_checksum  char(64),     -- SHA-256 of the source file
    filas_leidas     integer       CHECK (filas_leidas >= 0),
    filas_validas    integer       CHECK (filas_validas >= 0),
    -- Quality of the run (assignment, step 2): valid rows / rows read x 100.
    pct_validas      numeric(5,2)  GENERATED ALWAYS AS (
                         CASE WHEN filas_leidas > 0
                              THEN round(100.0 * filas_validas / filas_leidas, 2)
                         END
                     ) STORED,
    duracion_s       numeric(10,3) GENERATED ALWAYS AS (
                         EXTRACT(EPOCH FROM finished_at - started_at)
                     ) STORED,
    error_message    text,
    CONSTRAINT etl_run_log_validas_le_leidas CHECK (filas_validas <= filas_leidas),
    CONSTRAINT etl_run_log_finished_after_start CHECK (finished_at >= started_at)
);

COMMENT ON TABLE dq.etl_run_log IS 'One row per ETL run: counts, per-run quality and status.';
CREATE INDEX etl_run_log_started_at_idx ON dq.etl_run_log (started_at DESC);

CREATE TABLE dq.rule_result (
    run_id          uuid    NOT NULL REFERENCES dq.etl_run_log (run_id) ON DELETE CASCADE,
    rule_id         text    NOT NULL,  -- quality_rules[].id in the contract
    action          text    NOT NULL CHECK (action IN ('reject', 'flag', 'dedupe')),
    filas_afectadas integer NOT NULL CHECK (filas_afectadas >= 0),
    CONSTRAINT rule_result_pk PRIMARY KEY (run_id, rule_id)
);

COMMENT ON TABLE dq.rule_result IS 'Rows affected by each quality rule in each run.';

CREATE TABLE dq.fault_event (
    fault_id       bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dispositivo_id text        NOT NULL,
    rule_id        text        NOT NULL,  -- fault_rules[].id in the contract
    severity       text        NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    ts_inicio      timestamptz NOT NULL,  -- start of the first reading in the event
    ts_fin         timestamptz NOT NULL,  -- end of the last reading (exclusive)
    lecturas       integer     NOT NULL CHECK (lecturas > 0),
    run_id         uuid        NOT NULL REFERENCES dq.etl_run_log (run_id),
    detected_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fault_event_fin_after_inicio CHECK (ts_fin > ts_inicio),
    -- Re-detecting the same event on a re-run updates it instead of duplicating it.
    CONSTRAINT fault_event_uk UNIQUE (dispositivo_id, rule_id, ts_inicio)
);

COMMENT ON TABLE dq.fault_event IS
    'Plant faults detected on silver.lectura_5min (e.g. zero power in daylight).';
CREATE INDEX fault_event_ts_inicio_idx ON dq.fault_event (ts_inicio DESC);
