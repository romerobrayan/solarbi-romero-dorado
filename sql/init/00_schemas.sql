-- SolarBI Pascual - database bootstrap.
-- Runs once, as the owner role (POSTGRES_USER), when the data volume is empty.
-- Tables are NOT created here: they belong to later phases.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Medallion layers + data-quality audit.
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS dwh;
CREATE SCHEMA IF NOT EXISTS dq;

COMMENT ON SCHEMA bronze IS 'Raw telemetry exactly as received; never modified.';
COMMENT ON SCHEMA silver IS 'Cleaned and validated 5-minute readings (e.g. silver.lectura_5min).';
COMMENT ON SCHEMA dwh    IS 'Gold layer: star schema for consumers (e.g. dwh.fact_energia_dia).';
COMMENT ON SCHEMA dq     IS 'Data-quality audit: ETL run log and rule results (e.g. dq.etl_run_log).';

-- Read-only group role. Consumers (Grafana, Power BI) log in with their own
-- roles that inherit from it (created in 01_roles.sh, which needs env vars).
-- Bronze is deliberately excluded: consumers only see validated data.
CREATE ROLE bi_readonly NOLOGIN;
COMMENT ON ROLE bi_readonly IS 'Read-only access to silver, dwh and dq for BI consumers.';

GRANT USAGE ON SCHEMA silver, dwh, dq TO bi_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA silver, dwh, dq TO bi_readonly;

-- Tables, views and continuous aggregates created later by the owner (the ETL)
-- become readable automatically.
ALTER DEFAULT PRIVILEGES IN SCHEMA silver, dwh, dq GRANT SELECT ON TABLES TO bi_readonly;
