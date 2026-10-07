"""Retention per plan, the operational CLI, and the lazy database.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from tycheon_cp import cli
from tycheon_cp.db import Database
from tycheon_cp.retention import purge

pytestmark = [pytest.mark.postgres, pytest.mark.anyio]


async def seed(
    owner, org_id: str, *, days_old: int, reported: bool = True, kind="forecast_calls"
) -> None:
    when = datetime.now(UTC) - timedelta(days=days_old)
    await owner.execute(
        "INSERT INTO usage_events (org_id, kind, quantity, occurred_at, idempotency_key, reported_at) "
        "VALUES ($1::uuid, $2, 1, $3, $4, $5)",
        org_id,
        kind,
        when,
        uuid.uuid4().hex,
        when if reported else None,
    )
    await owner.execute(
        "INSERT INTO audit_log (org_id, actor, action, occurred_at) VALUES ($1::uuid, 'test', 'x', $2)",
        org_id,
        when,
    )


async def counts(owner, org_id: str) -> dict[str, int]:
    return {
        "usage": await owner.fetchval(
            "SELECT count(*) FROM usage_events WHERE org_id = $1::uuid", org_id
        ),
        "audit": await owner.fetchval(
            "SELECT count(*) FROM audit_log WHERE org_id = $1::uuid AND action = 'x'", org_id
        ),
    }


async def test_each_plan_keeps_data_for_its_own_retention_period(
    signup_org, set_org_plan, owner, cp
) -> None:
    dev, startup = await signup_org(), await signup_org()
    await set_org_plan(startup.org_id, "startup")
    for org in (dev, startup):
        await seed(owner, org.org_id, days_old=100)  # past the free plan's 30 days, within 365
        await seed(owner, org.org_id, days_old=5)
    totals = await purge(cp.db, cp.plans, cp.blobs)
    assert totals["usage_events"] >= 1 and totals["audit_log"] >= 1
    assert await counts(owner, dev.org_id) == {"usage": 1, "audit": 1}  # the old pair is gone
    assert await counts(owner, startup.org_id) == {"usage": 2, "audit": 2}  # nothing expired


async def test_unreported_usage_is_billing_data_and_is_kept(client, signup_org, owner, cp) -> None:
    org = await signup_org()
    await client.post("/v1/billing/portal", headers=org.session)  # gives the org a Stripe customer
    await seed(owner, org.org_id, days_old=400, reported=False)
    await seed(owner, org.org_id, days_old=400, reported=True)
    await purge(cp.db, cp.plans, cp.blobs)
    assert (await counts(owner, org.org_id))["usage"] == 1  # only the reported one went


async def test_audit_entries_inside_thirty_days_can_never_be_purged(db, signup_org) -> None:
    org = await signup_org()
    async with db.anonymous() as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="at least 30 days"):
            await conn.fetchval("SELECT cp_purge_audit($1::uuid, 5)", org.org_id)


async def test_purging_one_organisation_never_touches_another(
    signup_org, set_org_plan, owner, cp
) -> None:
    a, b = await signup_org(), await signup_org()
    await set_org_plan(b.org_id, "enterprise")  # seven years
    await seed(owner, a.org_id, days_old=200)
    await seed(owner, b.org_id, days_old=200)
    await purge(cp.db, cp.plans, cp.blobs)
    assert (await counts(owner, a.org_id))["audit"] == 0
    assert await counts(owner, b.org_id) == {"usage": 1, "audit": 1}


async def test_stale_models_and_their_artifacts_are_deleted_but_promoted_ones_stay(
    signup_org, owner, cp
) -> None:
    org = await signup_org()
    old = datetime.now(UTC) - timedelta(days=90)
    keys = {}
    for status in ("rejected", "archived", "promoted"):
        model_id = uuid.uuid4()
        key = f"orgs/{org.org_id}/models/{model_id}"
        cp.blobs.put(f"{key}/model.safetensors", b"weights")
        gate = json.dumps({"passed": status == "promoted"})
        await owner.execute(
            "INSERT INTO models (id, org_id, name, base_model, status, artifact_key, gate, created_at) "
            "VALUES ($1, $2::uuid, $3, 'kronos-mini', $4, $5, $6::jsonb, $7)",
            model_id,
            org.org_id,
            status,
            status,
            key,
            gate,
            old,
        )
        keys[status] = (model_id, key)
    totals = await purge(cp.db, cp.plans, cp.blobs)
    assert totals["models"] >= 2
    left = {
        r["status"]
        for r in await owner.fetch("SELECT status FROM models WHERE org_id = $1::uuid", org.org_id)
    }
    assert left == {"promoted"}
    assert not list(cp.blobs.root.rglob(f"{keys['rejected'][0]}"))
    assert list(cp.blobs.root.rglob(f"{keys['promoted'][0]}"))


# ------------------------------------------------------------------------------ the CLI
def test_the_cli_refuses_to_migrate_without_the_owner_url(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TYCHEON_CP_MIGRATION_URL", raising=False)
    assert cli.main(["migrate"]) == 2
    assert "TYCHEON_CP_MIGRATION_URL" in capsys.readouterr().err


def test_the_cli_reports_unsafe_configuration_instead_of_a_traceback(monkeypatch, capsys) -> None:
    for name in ("TYCHEON_CP_DATABASE_URL", "TYCHEON_CP_SESSION_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["purge"]) == 2
    assert "configuration error" in capsys.readouterr().err


async def test_migrating_twice_is_a_no_op(pg) -> None:
    from tycheon_cp.db import migrate

    assert await migrate(pg.owner, app_role="tycheon_app") == []


async def test_a_lazy_database_connects_on_first_use(pg) -> None:
    database = Database.lazy(pg.app)
    try:
        async with database.anonymous() as conn:
            assert await conn.fetchval("SELECT 1") == 1
    finally:
        await database.close()


def test_the_database_needs_a_pool_or_a_dsn() -> None:
    with pytest.raises(ValueError):
        Database()
