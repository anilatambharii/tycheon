"""Cross-tenant isolation at the database: row-level security, with a real Postgres.

Each test is an attack a buggy handler (or a missing ``WHERE org_id``) could allow. They run as the
unprivileged application role, so RLS is what stops them, not application code.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from tycheon_cp.testing import make_org

pytestmark = [pytest.mark.postgres, pytest.mark.anyio]

TENANT_TABLES = [
    "users",
    "api_keys",
    "credentials",
    "data_sources",
    "data_files",
    "usage_counters",
    "usage_events",
    "subscriptions",
    "oneoff_invoices",
    "oidc_providers",
    "finetune_jobs",
    "models",
    "routing_configs",
    "audit_log",
]


async def add_key(db, org, name="k"):
    async with db.tenant(org.org_id) as conn:
        return await conn.fetchval(
            "INSERT INTO api_keys (org_id, name, prefix, secret_hash, scopes) "
            "VALUES ($1, $2, $3, 'h', ARRAY['analytics']) RETURNING id",
            org.org_id,
            name,
            uuid.uuid4().hex[:12],
        )


async def test_the_application_role_is_neither_superuser_nor_bypassrls(db) -> None:
    async with db.anonymous() as conn:
        row = await conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
    assert row["rolsuper"] is False and row["rolbypassrls"] is False


async def test_every_tenant_table_has_rls_enabled_with_a_policy(owner) -> None:
    rows = await owner.fetch(
        "SELECT c.relname, c.relrowsecurity, "
        "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname = ANY($1)",
        [*TENANT_TABLES, "orgs"],
    )
    assert {r["relname"] for r in rows} == {*TENANT_TABLES, "orgs"}
    assert all(r["relrowsecurity"] and r["policies"] >= 1 for r in rows), [
        r["relname"] for r in rows if not r["relrowsecurity"]
    ]


async def test_a_tenant_sees_only_its_own_rows_even_when_asking_for_another(db) -> None:
    a, b = await make_org(db), await make_org(db)
    await add_key(db, a, "a-key")
    await add_key(db, b, "b-key")
    async with db.tenant(a.org_id) as conn:
        own = await conn.fetch("SELECT name FROM api_keys")
        asked_for_b = await conn.fetch("SELECT name FROM api_keys WHERE org_id = $1", b.org_id)
        by_id = await conn.fetch(
            "SELECT * FROM api_keys WHERE name = 'b-key' OR org_id <> $1", a.org_id
        )
    assert [r["name"] for r in own] == ["a-key"]
    assert asked_for_b == [] and by_id == []


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_without_a_tenant_no_row_of_any_table_is_visible(db, table) -> None:
    org = await make_org(db)
    await add_key(db, org)
    async with db.anonymous() as conn:
        count = await conn.fetchval(f"SELECT count(*) FROM {table}")
    assert count == 0


async def test_a_tenant_cannot_write_a_row_into_another_tenant(db) -> None:
    a, b = await make_org(db), await make_org(db)
    async with db.tenant(a.org_id) as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO api_keys (org_id, name, prefix, secret_hash, scopes) "
                "VALUES ($1, 'x', 'zzzzzzzzzzzz', 'h', ARRAY['analytics'])",
                b.org_id,
            )


async def test_a_tenant_cannot_update_or_delete_another_tenants_rows(db) -> None:
    a, b = await make_org(db), await make_org(db)
    await add_key(db, b, "victim")
    async with db.tenant(a.org_id) as conn:
        updated = await conn.execute("UPDATE api_keys SET name = 'pwned' WHERE name = 'victim'")
        deleted = await conn.execute("DELETE FROM api_keys WHERE name = 'victim'")
    assert updated == "UPDATE 0" and deleted == "DELETE 0"
    async with db.tenant(b.org_id) as conn:
        assert await conn.fetchval("SELECT name FROM api_keys") == "victim"


async def test_a_tenant_cannot_move_its_row_to_another_tenant(db) -> None:
    a, b = await make_org(db), await make_org(db)
    await add_key(db, a)
    async with db.tenant(a.org_id) as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("UPDATE api_keys SET org_id = $1", b.org_id)


async def test_the_org_setting_does_not_outlive_its_transaction(db) -> None:
    a = await make_org(db)
    await add_key(db, a)
    async with db.tenant(a.org_id) as conn:
        assert await conn.fetchval("SELECT count(*) FROM api_keys") == 1
    # the pool hands connections back and forth: none may remember the previous tenant
    for _ in range(20):
        async with db.anonymous() as conn:
            assert await conn.fetchval("SELECT count(*) FROM api_keys") == 0
            assert await conn.fetchval("SELECT current_setting('app.org_id', true)") in ("", None)


async def test_only_a_uuid_can_name_a_tenant(db) -> None:
    for bad in ("1 OR 1=1", "'; DROP TABLE orgs;--", "", "not-a-uuid"):
        with pytest.raises(ValueError, match=r"UUID|badly formed"):
            async with db.tenant(bad):
                pass


async def test_the_pre_tenant_lookups_return_only_what_they_must(db) -> None:
    a = await make_org(db)
    async with db.anonymous() as conn:
        login = await conn.fetch("SELECT * FROM cp_login_lookup($1)", a.email)
        nobody = await conn.fetch("SELECT * FROM cp_login_lookup($1)", "nobody@example.com")
    assert len(login) == 1 and login[0]["org_id"] == a.org_id and nobody == []


async def test_the_audit_log_is_append_only_for_the_application(db) -> None:
    a = await make_org(db)
    async with db.tenant(a.org_id) as conn:
        assert await conn.fetchval("SELECT count(*) FROM audit_log") >= 1  # the signup entry
        await conn.execute(
            "INSERT INTO audit_log (org_id, actor, action) VALUES ($1, 'test', 'x')", a.org_id
        )
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("UPDATE audit_log SET action = 'forged'")
    async with db.tenant(a.org_id) as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("DELETE FROM audit_log")


async def test_the_application_cannot_read_or_write_webhook_events_or_run_ddl(db) -> None:
    async with db.anonymous() as conn:
        event_id = f"evt_{uuid.uuid4().hex}"
        assert await conn.fetchval("SELECT cp_record_stripe_event($1)", event_id) is True
        assert await conn.fetchval("SELECT cp_record_stripe_event($1)", event_id) is False
    for statement in ("SELECT * FROM stripe_events", "INSERT INTO stripe_events (id) VALUES ('x')"):
        async with db.anonymous() as conn:
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.execute(statement)
    async with db.anonymous() as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("CREATE TABLE evil (x int)")
