-- Runs once, on an empty Postgres data directory, via docker-entrypoint-initdb.d.
-- Keep this to extensions and schemas only; application tables arrive with
-- migrations in a later phase.

-- Deterministic UUIDs for run ids.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Schemas mirror the phases that will own them.
CREATE SCHEMA IF NOT EXISTS forecasts;   -- forecast records and their model mix
CREATE SCHEMA IF NOT EXISTS backtests;   -- walk-forward runs, metrics, baselines
CREATE SCHEMA IF NOT EXISTS registry;    -- model cards and calibration status

COMMENT ON SCHEMA forecasts IS 'Forecast outputs: intervals, calibration status, model mix, as_of.';
COMMENT ON SCHEMA backtests IS 'Walk-forward evaluation runs, including baseline and Diebold-Mariano results.';
COMMENT ON SCHEMA registry IS 'Model cards, versions and calibration state.';
