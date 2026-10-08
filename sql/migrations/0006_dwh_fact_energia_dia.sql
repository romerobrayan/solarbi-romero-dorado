-- Gold fact: daily energy per device.
-- Grain: one row per LOCAL calendar day of the site and device, never the UTC day
-- (docs/adr/0004). Loaded by the ETL with an idempotent UPSERT:
--   INSERT ... ON CONFLICT (fecha_key, dispositivo_key) DO UPDATE SET ...

CREATE TABLE dwh.fact_energia_dia (
    fecha_key          integer       NOT NULL REFERENCES dwh.dim_fecha (fecha_key),
    dispositivo_key    integer       NOT NULL REFERENCES dwh.dim_dispositivo (dispositivo_key),
    -- energia_kwh = SUM(p_ac_kw * frequency_seconds / 3600) over the day's valid readings;
    -- with the contract's 300 s frequency that is SUM(p_ac_kw * 5 / 60).
    energia_kwh        numeric(12,4) NOT NULL CHECK (energia_kwh >= 0),
    lecturas_validas   integer       NOT NULL CHECK (lecturas_validas >= 0),
    -- 86 400 / frequency_seconds from the contract (288 for 5-minute readings).
    lecturas_esperadas integer       NOT NULL CHECK (lecturas_esperadas > 0),
    -- Completeness of the day: unique valid readings / expected readings x 100.
    pct_datos_validos  numeric(5,2)  GENERATED ALWAYS AS (
                           round(100.0 * lecturas_validas / lecturas_esperadas, 2)
                       ) STORED,
    -- pct_datos_validos >= sla.pct_datos_validos_min in the contract (95 today).
    cumple_sla         boolean       NOT NULL,
    p_max_kw           numeric(10,3) CHECK (p_max_kw >= 0),
    -- irradiacion_kwh_m2 = SUM(irradiancia_wm2 * frequency_seconds / 3600) / 1000 over
    -- readings without irradiance flags; input for yield and Performance Ratio.
    irradiacion_kwh_m2 numeric(10,4) CHECK (irradiacion_kwh_m2 >= 0),
    run_id             uuid          NOT NULL REFERENCES dq.etl_run_log (run_id),
    updated_at         timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT fact_energia_dia_pk PRIMARY KEY (fecha_key, dispositivo_key)
);

COMMENT ON TABLE dwh.fact_energia_dia IS
    'Daily energy per device (local day). Idempotent UPSERT on (fecha_key, dispositivo_key).';
