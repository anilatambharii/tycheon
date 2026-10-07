-- Local development and CI: the two Postgres roles Tycheon Cloud needs.
-- Run as a superuser:  psql -f ee/scripts/create_roles.sql
-- The passwords below are for throwaway local databases only. In production use your own
-- secrets management and never reuse these.
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tycheon_owner') THEN
    CREATE ROLE tycheon_owner LOGIN PASSWORD 'owner-dev-pw' CREATEROLE CREATEDB;  -- pragma: allowlist secret
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tycheon_app') THEN
    -- the application role: no superuser, no BYPASSRLS, owns nothing
    CREATE ROLE tycheon_app LOGIN PASSWORD 'app-dev-pw' NOSUPERUSER NOBYPASSRLS;  -- pragma: allowlist secret
  END IF;
END $$;
SELECT 'CREATE DATABASE tycheon_test OWNER tycheon_owner'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'tycheon_test')\gexec
