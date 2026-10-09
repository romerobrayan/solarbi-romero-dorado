-- Day purges (scripts/purge_days.py): one row per purge, with what it removed.
--
-- Bronze is append-only (its trigger rejects UPDATE/DELETE), so a purge cannot
-- delete or flag the raw rows. Instead this row IS the "superseded" mark: Bronze
-- rows whose 5-minute slot falls in [ts_desde, ts_hasta) and that were loaded
-- before ejecutada_en are superseded, and the ETL skips them (etl/sqlgen.py,
-- stage_sql). Re-running an old file therefore cannot bring purged days back;
-- rows loaded after the purge (a new file, --force-reload, the live replay) are
-- current again.

CREATE TABLE dq.purga_log (
    purga_id      uuid        PRIMARY KEY,
    dia_desde     date        NOT NULL,  -- first local day purged
    dia_hasta     date        NOT NULL,  -- last local day purged (inclusive)
    zona_horaria  text        NOT NULL,  -- the site's time zone that defines the days
    ts_desde      timestamptz NOT NULL,  -- dia_desde 00:00 local, in UTC
    ts_hasta      timestamptz NOT NULL,  -- the day after dia_hasta 00:00 local (exclusive)
    motivo        text        NOT NULL CHECK (btrim(motivo) <> ''),
    ejecutada_en  timestamptz NOT NULL DEFAULT now(),
    ejecutada_por text        NOT NULL DEFAULT current_user,
    filas_bronze_reemplazadas integer NOT NULL CHECK (filas_bronze_reemplazadas >= 0),
    filas_silver  integer     NOT NULL CHECK (filas_silver >= 0),
    filas_gold    integer     NOT NULL CHECK (filas_gold >= 0),
    eventos_falla integer     NOT NULL CHECK (eventos_falla >= 0),
    CONSTRAINT purga_log_rango CHECK (dia_hasta >= dia_desde AND ts_hasta > ts_desde)
);

COMMENT ON TABLE dq.purga_log IS
    'Day purges: Silver/Gold/fault rows removed, and the mark that supersedes older Bronze rows of those days.';
CREATE INDEX purga_log_rango_idx ON dq.purga_log (ts_desde, ts_hasta);
