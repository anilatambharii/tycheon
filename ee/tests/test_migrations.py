"""Migrations and rollback, each in a scratch database (never the shared test schema).

The strongest claim here: rolling back to migration *k* leaves a schema identical to a fresh
database migrated only to *k*, for every *k*. Also: a rollback is atomic, refuses to drop tables
without being told to, and re-applying after a rollback works.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import uuid
from importlib import resources
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from tycheon_cp.db import RollbackRefusedError, migrate, rollback

pytestmark = [pytest.mark.postgres, pytest.mark.anyio]

APP_ROLE = "tycheon_app"


def with_database(dsn: str, name: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path=f"/{name}"))


@pytest.fixture
async def scratch(pg):
    """A throwaway database owned by the owner role; dropped afterwards."""
    name = f"tycheon_mig_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(pg.owner)
    await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield with_database(pg.owner, name), admin, name
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def shape(dsn: str) -> dict[str, list]:
    """Everything about the schema that a migration could change."""
    conn = await asyncpg.connect(dsn)
    try:
        return {
            "columns": [
                tuple(r)
                for r in await conn.fetch(
                    "SELECT table_name, column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name <> 'schema_migrations' ORDER BY 1, 2"
                )
            ],
            "functions": [
                tuple(r)
                for r in await conn.fetch(
                    "SELECT p.proname, pg_get_function_arguments(p.oid), p.prosecdef "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' ORDER BY 1, 2"
                )
            ],
            "constraints": [
                tuple(r)
                for r in await conn.fetch(
                    "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
                    "FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2"
                )
            ],
            "policies": [
                tuple(r)
                for r in await conn.fetch(
                    "SELECT tablename, policyname, cmd FROM pg_policies ORDER BY 1, 2"
                )
            ],
            "table_grants": [
                tuple(r)
                for r in await conn.fetch(
                    "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = $1 AND table_schema = 'public' ORDER BY 1, 2",
                    APP_ROLE,
                )
            ],
            "function_grants": [
                tuple(r)
                for r in await conn.fetch(
                    "SELECT routine_name FROM information_schema.routine_privileges "
                    "WHERE grantee = $1 AND routine_schema = 'public' ORDER BY 1",
                    APP_ROLE,
                )
            ],
        }
    finally:
        await conn.close()


def up_names() -> list[str]:
    folder = resources.files("tycheon_cp").joinpath("migrations")
    return sorted(
        e.name
        for e in folder.iterdir()
        if e.name.endswith(".sql") and not e.name.endswith(".down.sql")
    )


def test_every_migration_has_a_down_migration() -> None:
    folder = resources.files("tycheon_cp").joinpath("migrations")
    names = {e.name for e in folder.iterdir()}
    for up in up_names():
        assert up.removesuffix(".sql") + ".down.sql" in names, f"{up} has no down migration"
    assert len(up_names()) >= 5


def test_migration_names_are_ordered_and_gapless() -> None:
    numbers = [int(n.split("_")[0]) for n in up_names()]
    assert numbers == list(range(1, len(numbers) + 1))


async def test_down_files_are_never_applied_as_up_migrations(scratch) -> None:
    dsn, _, _ = scratch
    applied = await migrate(dsn, app_role=APP_ROLE)
    assert applied == up_names()
    assert await migrate(dsn, app_role=APP_ROLE) == []  # idempotent


@pytest.mark.parametrize("k", range(1, 5))
async def test_rolling_back_to_a_version_equals_migrating_fresh_to_it(pg, scratch, k) -> None:
    ups = up_names()
    dsn_full, admin, _ = scratch
    await migrate(dsn_full, app_role=APP_ROLE)
    steps = len(ups) - k
    undone = await rollback(dsn_full, app_role=APP_ROLE, steps=steps, allow_data_loss=True)
    assert undone == list(reversed(ups[k:]))

    fresh_name = f"tycheon_mig_{uuid.uuid4().hex[:10]}"
    await admin.execute(f'CREATE DATABASE "{fresh_name}"')
    try:
        fresh = with_database(pg.owner, fresh_name)
        await migrate(fresh, app_role=APP_ROLE, target=ups[k - 1])
        assert await shape(dsn_full) == await shape(fresh)
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{fresh_name}" WITH (FORCE)')


async def test_a_rollback_can_be_applied_again(scratch) -> None:
    dsn, _, _ = scratch
    await migrate(dsn, app_role=APP_ROLE)
    before = await shape(dsn)
    await rollback(dsn, app_role=APP_ROLE, steps=2, allow_data_loss=True)
    assert await migrate(dsn, app_role=APP_ROLE) == up_names()[-2:]
    assert await shape(dsn) == before


async def test_a_rollback_that_drops_tables_is_refused_without_the_flag(scratch) -> None:
    dsn, _, _ = scratch
    await migrate(dsn, app_role=APP_ROLE)
    with pytest.raises(RollbackRefusedError, match="drops tables"):
        await rollback(dsn, app_role=APP_ROLE, steps=len(up_names()))
    # nothing was undone: the refusal happens before any change
    assert await migrate(dsn, app_role=APP_ROLE) == []


async def test_with_the_flag_an_empty_database_rolls_back_to_nothing(scratch) -> None:
    dsn, _, _ = scratch
    await migrate(dsn, app_role=APP_ROLE)
    undone = await rollback(dsn, app_role=APP_ROLE, steps=len(up_names()), allow_data_loss=True)
    assert len(undone) == len(up_names())
    conn = await asyncpg.connect(dsn)
    try:
        left = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
            "AND table_name <> 'schema_migrations'"
        )
    finally:
        await conn.close()
    assert left == []


async def test_a_rollback_keeps_the_data_of_the_migrations_that_stay(scratch) -> None:
    dsn, _, _ = scratch
    await migrate(dsn, app_role=APP_ROLE)
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT * FROM cp_signup($1, $2, $3, $4)", "keeper", "Keeper", "k@example.com", "x"
        )
        assert row is not None
    finally:
        await conn.close()
    await rollback(dsn, app_role=APP_ROLE, steps=2, allow_data_loss=True)  # the newest two
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT count(*) FROM orgs WHERE slug = 'keeper'") == 1
    finally:
        await conn.close()


async def test_a_failing_down_migration_leaves_everything_as_it_was(scratch, monkeypatch) -> None:
    from tycheon_cp import db

    dsn, _, _ = scratch
    await migrate(dsn, app_role=APP_ROLE)
    up, down = db._scripts()
    broken = {**down, up_names()[-1]: "SELECT 1; SELECT * FROM does_not_exist;"}
    monkeypatch.setattr(db, "_scripts", lambda: (up, broken))
    before = await shape(dsn)
    with pytest.raises(asyncpg.PostgresError):
        await rollback(dsn, app_role=APP_ROLE, steps=1, allow_data_loss=True)
    monkeypatch.undo()
    assert await shape(dsn) == before
    assert await migrate(dsn, app_role=APP_ROLE) == []  # still recorded as applied


async def test_bad_arguments_are_refused(scratch) -> None:
    dsn, _, _ = scratch
    with pytest.raises(ValueError):
        await rollback(dsn, app_role=APP_ROLE, steps=0)
    with pytest.raises(ValueError):
        await rollback(dsn, app_role="x; DROP TABLE users", steps=1)
    with pytest.raises(ValueError):
        await migrate(dsn, app_role=APP_ROLE, target="9999_nope.sql")
