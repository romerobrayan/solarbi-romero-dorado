#!/usr/bin/env bash
# SolarBI Pascual - read-only login roles for the BI consumers.
#
# Plain .sql init files cannot read environment variables, so this script
# passes the passwords to psql as variables (:'name' quotes them safely).
# It runs once, after 00_schemas.sql, when the data volume is empty.
# To change a password later, see "Credenciales" in README.md.
set -Eeo pipefail

: "${GRAFANA_READER_PASSWORD:?GRAFANA_READER_PASSWORD is not set}"
: "${POWERBI_READER_PASSWORD:?POWERBI_READER_PASSWORD is not set}"

psql -v ON_ERROR_STOP=1 --no-psqlrc \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v grafana_password="$GRAFANA_READER_PASSWORD" \
    -v powerbi_password="$POWERBI_READER_PASSWORD" <<'EOSQL'
CREATE ROLE grafana_reader LOGIN PASSWORD :'grafana_password' IN ROLE bi_readonly;
CREATE ROLE powerbi_reader LOGIN PASSWORD :'powerbi_password' IN ROLE bi_readonly;

COMMENT ON ROLE grafana_reader IS 'Grafana datasource (operator dashboards). Read-only.';
COMMENT ON ROLE powerbi_reader IS 'Power BI Desktop (management reports). Read-only.';

-- Defense in depth: even a mistaken grant cannot turn these sessions into writers.
ALTER ROLE grafana_reader SET default_transaction_read_only = on;
ALTER ROLE powerbi_reader SET default_transaction_read_only = on;

-- Real-time panels must stay fast; a runaway query is cancelled, not queued.
ALTER ROLE grafana_reader SET statement_timeout = '30s';
EOSQL
