-- Gold dimensions of the star schema: date, site and device.
-- dim_sitio and dim_dispositivo are filled by the ETL from the contract's sites
-- and devices sections (Phase 2); dim_fecha is filled here for 2020-2035.

CREATE TABLE dwh.dim_fecha (
    fecha_key        integer  PRIMARY KEY,  -- YYYYMMDD of the LOCAL calendar day
    fecha            date     NOT NULL UNIQUE,
    anio             smallint NOT NULL,
    trimestre        smallint NOT NULL,
    mes              smallint NOT NULL,
    nombre_mes       text     NOT NULL,
    dia              smallint NOT NULL,
    dia_semana       smallint NOT NULL,     -- ISO 8601: 1 = lunes ... 7 = domingo
    nombre_dia       text     NOT NULL,
    semana_iso       smallint NOT NULL,
    es_fin_de_semana boolean  NOT NULL
);

COMMENT ON TABLE dwh.dim_fecha IS 'Calendar dimension; one row per local calendar day.';

-- Adds the missing days in [p_start, p_end] and returns how many were added.
-- Idempotent: the ETL can call it for the date range of each load.
CREATE FUNCTION dwh.ensure_dim_fecha(p_start date, p_end date) RETURNS integer
LANGUAGE sql AS $$
    WITH days AS (
        SELECT p_start + offset_days AS fecha
        FROM generate_series(0, p_end - p_start) AS offset_days
    ),
    inserted AS (
        INSERT INTO dwh.dim_fecha (
            fecha_key, fecha, anio, trimestre, mes, nombre_mes, dia,
            dia_semana, nombre_dia, semana_iso, es_fin_de_semana
        )
        SELECT
            (EXTRACT(YEAR FROM fecha) * 10000 + EXTRACT(MONTH FROM fecha) * 100
                + EXTRACT(DAY FROM fecha))::integer,
            fecha,
            EXTRACT(YEAR FROM fecha)::smallint,
            EXTRACT(QUARTER FROM fecha)::smallint,
            EXTRACT(MONTH FROM fecha)::smallint,
            (ARRAY['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
                   'septiembre', 'octubre', 'noviembre', 'diciembre'])[EXTRACT(MONTH FROM fecha)::int],
            EXTRACT(DAY FROM fecha)::smallint,
            EXTRACT(ISODOW FROM fecha)::smallint,
            (ARRAY['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado',
                   'domingo'])[EXTRACT(ISODOW FROM fecha)::int],
            EXTRACT(WEEK FROM fecha)::smallint,
            EXTRACT(ISODOW FROM fecha) >= 6
        FROM days
        ON CONFLICT (fecha_key) DO NOTHING
        RETURNING 1
    )
    SELECT count(*)::integer FROM inserted;
$$;

REVOKE EXECUTE ON FUNCTION dwh.ensure_dim_fecha(date, date) FROM PUBLIC;

SELECT dwh.ensure_dim_fecha(DATE '2020-01-01', DATE '2035-12-31');

CREATE TABLE dwh.dim_sitio (
    sitio_key    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sitio_id     text    NOT NULL UNIQUE,  -- natural key: sites[].sitio_id in the contract
    nombre       text    NOT NULL,
    ciudad       text,
    zona_horaria text    NOT NULL          -- IANA name; defines the site's local day
);

COMMENT ON TABLE dwh.dim_sitio IS 'Plant sites (seeded from the contract).';

CREATE TABLE dwh.dim_dispositivo (
    dispositivo_key integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dispositivo_id  text          NOT NULL UNIQUE,  -- natural key from the telemetry
    nombre          text          NOT NULL,
    tipo            text,
    nominal_kwp     numeric(8,3)  NOT NULL CHECK (nominal_kwp > 0),
    sitio_key       integer       NOT NULL REFERENCES dwh.dim_sitio (sitio_key)
);

COMMENT ON TABLE dwh.dim_dispositivo IS 'Inverters (seeded from the contract). SCD type 1.';
