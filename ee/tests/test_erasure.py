"""Organisation erasure: every row and file goes, other tenants stay, failures leave it safe.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import hashlib

import asyncpg
import pytest

from tycheon_cp import erasure
from tycheon_cp.security import Session, issue_session

pytestmark = [pytest.mark.postgres, pytest.mark.anyio]

TENANT_TABLES = (
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
)


async def slug_of(owner, org_id: str) -> str:
    return await owner.fetchval("SELECT slug FROM orgs WHERE id = $1::uuid", org_id)


async def rows(owner, org_id: str) -> dict[str, int]:
    out = {}
    for table in TENANT_TABLES:
        out[table] = await owner.fetchval(
            f"SELECT count(*) FROM {table} WHERE org_id = $1::uuid",
            org_id,
        )
    out["orgs"] = await owner.fetchval("SELECT count(*) FROM orgs WHERE id = $1::uuid", org_id)
    return out


async def erase(client, tenant, slug: str, headers=None):
    return await client.post(
        "/v1/org/erase", json={"confirm_slug": slug}, headers=headers or tenant.session
    )


async def test_erasing_removes_every_row_and_file_and_leaves_a_receipt(
    client, signup_org, csv_factory, owner, cp
) -> None:
    a = await signup_org()
    await a.make_key()
    await a.upload("MYCO", csv_factory())
    slug = await slug_of(owner, a.org_id)
    before = await rows(owner, a.org_id)
    assert before["users"] == 1 and before["api_keys"] == 1 and before["data_files"] == 1
    org_dir = cp.blobs.root / "orgs" / a.org_id
    assert any(org_dir.rglob("*"))

    res = await erase(client, a, slug)
    assert res.status_code == 200, res.text
    assert res.json()["files_erased"] is True

    assert all(v == 0 for v in (await rows(owner, a.org_id)).values())
    assert not org_dir.exists()
    receipt = await owner.fetchrow(
        "SELECT * FROM erasure_receipts WHERE org_id = $1::uuid", a.org_id
    )
    assert receipt["slug_sha256"] == hashlib.sha256(slug.encode()).hexdigest()
    assert receipt["actor_role"] == "owner" and receipt["blobs_erased_at"] is not None
    # the session and the key the organisation held no longer work
    assert (await client.get("/v1/me", headers=a.session)).status_code >= 400
    assert (await client.get("/v1/api-keys", headers=a.session)).status_code >= 400


async def test_the_receipt_holds_no_personal_data(client, signup_org, owner) -> None:
    a = await signup_org()
    slug = await slug_of(owner, a.org_id)
    await erase(client, a, slug)
    receipt = await owner.fetchrow(
        "SELECT * FROM erasure_receipts WHERE org_id = $1::uuid", a.org_id
    )
    text = " ".join(str(v) for v in dict(receipt).values())
    assert a.email not in text and slug not in text


async def test_other_organisations_are_untouched(client, signup_org, csv_factory, owner) -> None:
    a, b = await signup_org(), await signup_org()
    await b.make_key()
    await b.upload("MYCO", csv_factory())
    before = await rows(owner, b.org_id)
    assert (await erase(client, a, await slug_of(owner, a.org_id))).status_code == 200
    assert await rows(owner, b.org_id) == before
    assert (await client.get("/v1/me", headers=b.session)).status_code == 200


async def test_the_wrong_confirmation_erases_nothing(client, signup_org, owner) -> None:
    a = await signup_org()
    before = await rows(owner, a.org_id)
    res = await erase(client, a, "not-my-slug")
    assert res.status_code == 422
    assert await rows(owner, a.org_id) == before


async def test_only_a_signed_in_owner_may_erase(client, signup_org, owner, cp) -> None:
    a = await signup_org()
    slug = await slug_of(owner, a.org_id)
    for role in ("admin", "member", "viewer"):
        token = issue_session(cp.session_secret, Session(a.user_id, a.org_id, role), ttl_seconds=60)
        res = await erase(client, a, slug, {"Authorization": f"Bearer {token}"})
        assert res.status_code == 403, role
    key = await a.make_key()
    assert (await erase(client, a, slug, {"X-API-Key": key})).status_code in {401, 403}
    assert (await rows(owner, a.org_id))["orgs"] == 1


async def test_a_running_finetune_job_blocks_the_erasure(
    client, signup_org, owner, csv_factory
) -> None:
    a = await signup_org()
    uploaded = await a.upload("MYCO", csv_factory())
    await owner.execute(
        "INSERT INTO finetune_jobs (org_id, data_source_id, symbols, params, status, gpu_pool) "
        "VALUES ($1::uuid, $2::uuid, ARRAY['MYCO'], '{}'::jsonb, 'running', 'cpu')",
        a.org_id,
        uploaded["source_id"],
    )
    res = await erase(client, a, await slug_of(owner, a.org_id))
    assert res.status_code == 409
    assert (await rows(owner, a.org_id))["orgs"] == 1


async def test_the_subscription_is_cancelled_and_a_stripe_failure_stops_everything(
    client, signup_org, owner, fake_stripe
) -> None:
    a = await signup_org()
    await owner.execute(
        "UPDATE subscriptions SET stripe_customer_id = 'cus_x', stripe_subscription_id = 'sub_x' "
        "WHERE org_id = $1::uuid",
        a.org_id,
    )
    slug = await slug_of(owner, a.org_id)
    fake_stripe.fail_cancel = True
    assert (await erase(client, a, slug)).status_code == 502
    assert (await rows(owner, a.org_id))["orgs"] == 1  # nothing erased while still billed
    fake_stripe.fail_cancel = False
    assert (await erase(client, a, slug)).status_code == 200
    assert fake_stripe.canceled == ["sub_x"]


async def test_the_database_function_refuses_the_wrong_tenant_context(
    db, signup_org, owner
) -> None:
    a, b = await signup_org(), await signup_org()
    async with db.tenant(b.org_id) as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="own tenant context"):
            await conn.fetchval("SELECT cp_erase_org($1::uuid, 'owner')", a.org_id)
    assert (await rows(owner, a.org_id))["orgs"] == 1


async def test_failed_file_erasure_stays_pending_until_it_is_finished(
    client, signup_org, csv_factory, owner, cp, monkeypatch
) -> None:
    a = await signup_org()
    await a.upload("MYCO", csv_factory())
    real = cp.blobs.delete_org

    def broken(org_id: str) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(cp.blobs, "delete_org", broken)
    res = await erase(client, a, await slug_of(owner, a.org_id))
    assert res.status_code == 200 and res.json()["files_erased"] is False
    assert (await rows(owner, a.org_id))["orgs"] == 0  # the rows are already gone
    assert await owner.fetchval("SELECT count(*) FROM cp_pending_erasures()") >= 1

    monkeypatch.setattr(cp.blobs, "delete_org", real)
    assert await erasure.finish_pending(cp.db, cp.blobs) >= 1
    assert await owner.fetchval(
        "SELECT blobs_erased_at IS NOT NULL FROM erasure_receipts WHERE org_id = $1::uuid",
        a.org_id,
    )
