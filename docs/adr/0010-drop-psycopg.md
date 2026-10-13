# ADR 0010: Drop `psycopg` from the open-source `serve` extra

Status: accepted. For research and risk analytics. Not investment advice.

## Context
The Phase T0 `serve` extra listed `psycopg[binary,pool]` (LGPL-3.0) in anticipation of a Postgres
store in the open-source API. The Apache-2.0 package never used it: no module under `src/` imports
it, and the open-source API keeps its state in memory and DuckDB. Tycheon Cloud (`ee/`) talks to
Postgres through `asyncpg` (Apache-2.0) with plain SQL (ADR 0009). The launch checklist (risk 11)
flagged the LGPL dependency as a licence question for an Apache-2.0 distribution.

## Decision
Remove `psycopg` from the `serve` extra and the lock file. There was no code to port. If the
open-source API later needs Postgres, use `asyncpg` (BSD/Apache-style licence), not `psycopg`.

## Consequences
* The installed `tycheon[serve]` tree has no LGPL runtime dependency from this source.
* `psycopg-binary` and `psycopg-pool` leave the lock file.
* Guarded by a test that fails if `psycopg` reappears in `pyproject.toml`.
