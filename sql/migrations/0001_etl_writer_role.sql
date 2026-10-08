-- migrate:run-as owner
--
-- ETL role and the privileges it needs. Runs as the database owner because it
-- creates a role and grants on schemas the owner created in sql/init/.
-- The role starts as NOLOGIN: scripts/migrate.py then enables LOGIN with the
-- password from ETL_WRITER_PASSWORD (SQL files cannot read environment variables).

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'etl_writer') THEN
        CREATE ROLE etl_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END
$$;

COMMENT ON ROLE etl_writer IS
    'Runs the ETL and owns the tables in bronze, silver, dwh and dq. Not a superuser.';

GRANT USAGE, CREATE ON SCHEMA bronze, silver, dwh, dq, meta TO etl_writer;

-- The ALTER DEFAULT PRIVILEGES in sql/init/00_schemas.sql only covers objects the
-- owner creates. Every table is created by etl_writer, so the read-only consumers
-- need the same rule for objects etl_writer creates (bronze stays excluded).
ALTER DEFAULT PRIVILEGES FOR ROLE etl_writer IN SCHEMA silver, dwh, dq
    GRANT SELECT ON TABLES TO bi_readonly;
