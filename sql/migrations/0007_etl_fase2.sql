-- What the Phase 2 ETL records beyond the Phase 1 tables.

-- Readings within the grid tolerance are snapped to their 5-minute slot (contract
-- grid section); the timestamp as received is kept here. NULL = already on the slot.
ALTER TABLE silver.lectura_5min ADD COLUMN ts_origen timestamptz;
COMMENT ON COLUMN silver.lectura_5min.ts_origen IS
    'Original timestamp (UTC) when the reading was snapped to its slot; NULL if it was on the slot.';

-- Bronze is loaded once per file (by SHA-256). A re-run of the same file reuses
-- the Bronze rows of bronze_run_id and still recomputes Silver, Gold and faults.
ALTER TABLE dq.etl_run_log
    ADD COLUMN bronze_status text
        CONSTRAINT etl_run_log_bronze_status CHECK (bronze_status IN ('loaded', 'skipped_duplicate_file')),
    ADD COLUMN bronze_run_id uuid REFERENCES dq.etl_run_log (run_id),
    -- Step 2 report, distinct rows: leidas = validas + rechazadas + deduplicadas.
    ADD COLUMN filas_rechazadas   integer CHECK (filas_rechazadas >= 0),
    ADD COLUMN filas_deduplicadas integer CHECK (filas_deduplicadas >= 0),
    ADD COLUMN filas_marcadas     integer CHECK (filas_marcadas >= 0),  -- valid rows with a flag
    ADD COLUMN dias_gold          integer CHECK (dias_gold >= 0),       -- (day, device) rows upserted
    ADD COLUMN eventos_falla      integer CHECK (eventos_falla >= 0),
    ADD CONSTRAINT etl_run_log_cuadre
        CHECK (filas_leidas = filas_validas + filas_rechazadas + filas_deduplicadas);

COMMENT ON COLUMN dq.etl_run_log.bronze_run_id IS
    'Run whose bronze.telemetria_raw rows were transformed (itself, or the run that loaded the file).';

-- Finds an already loaded file by checksum without scanning the whole log.
CREATE INDEX etl_run_log_checksum_idx ON dq.etl_run_log (source_checksum)
    WHERE status = 'succeeded';
