-- Bronze landing table: every source row exactly as read, every value as text.
-- Loaded with COPY in Phase 2 (the ELT path that scales to millions of rows).
-- Data columns have no constraints, so nothing is ever rejected at this layer;
-- they are named after columns[].source_name in contracts/telemetria.yaml.

CREATE TABLE bronze.telemetria_raw (
    run_id          uuid        NOT NULL,  -- dq.etl_run_log.run_id (no FK: keep COPY fast)
    source_file     text        NOT NULL,
    source_row      integer     NOT NULL,  -- 1-based data row in the file, header excluded
    loaded_at       timestamptz NOT NULL DEFAULT now(),
    ts              text,
    dispositivo_id  text,
    p_ac_kw         text,
    irradiancia_wm2 text,
    temp_modulo_c   text
);

COMMENT ON TABLE bronze.telemetria_raw IS
    'Raw telemetry rows as received (all text). Append-only: updates, deletes and truncates are rejected.';
CREATE INDEX telemetria_raw_run_id_idx ON bronze.telemetria_raw (run_id);

-- Bronze is immutable: enforce append-only in the database, not just by convention.
CREATE FUNCTION bronze.reject_modification() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'bronze.% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
        USING HINT = 'Bronze is immutable; load a new run instead of editing rows.';
END
$$;

CREATE TRIGGER telemetria_raw_append_only
    BEFORE UPDATE OR DELETE ON bronze.telemetria_raw
    FOR EACH ROW EXECUTE FUNCTION bronze.reject_modification();

CREATE TRIGGER telemetria_raw_no_truncate
    BEFORE TRUNCATE ON bronze.telemetria_raw
    FOR EACH STATEMENT EXECUTE FUNCTION bronze.reject_modification();
