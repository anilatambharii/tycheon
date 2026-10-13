"""Organisation erasure end to end: request, grace period, execution, receipts, restore, export.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from tests.conftest import METRICS_TOKEN

from tycheon_cp import erasure, security
from tycheon_cp.erasure_routes import billing_factory
from tycheon_cp.security import Session, issue_session
from tycheon_cp.tombstones import Tombstones

pytestmark = [pytest.mark.postgres, pytest.mark.anyio]

PASSWORD = "correct horse battery"  # pragma: allowlist secret
WRONG_PASSWORD = "not the password"  # pragma: allowlist secret
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
    "erasure_requests",
)


class Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[list[str], str, str]] = []

    async def send(self, recipients: list[str], subject: str, body: str) -> None:
        self.sent.append((recipients, subject, body))


class FakeModels:
    def __init__(self) -> None:
        self.evicted: list[str] = []

    def evict_org(self, org_id) -> int:
        self.evicted.append(str(org_id))
        return 1


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


async def request(client, tenant, slug: str, *, password: str | None = PASSWORD, headers=None):
    body = {"confirm_slug": slug}
    if password is not None:
        body["password"] = password
    return await client.post("/v1/org/erase", json=body, headers=headers or tenant.session)


async def make_due(owner, org_id: str) -> None:
    await owner.execute(
        "UPDATE erasure_requests SET execute_after = now() - interval '1 minute' "
        "WHERE org_id = $1::uuid",
        org_id,
    )


async def sweep(cp, fake_stripe) -> dict[str, int]:
    return await erasure.run_due(cp, lambda: billing_factory(cp, fake_stripe))


async def schedule_and_run(client, tenant, owner, cp, fake_stripe) -> dict[str, int]:
    res = await request(client, tenant, await slug_of(owner, tenant.org_id))
    assert res.status_code == 202, res.text
    await make_due(owner, tenant.org_id)
    return await sweep(cp, fake_stripe)


async def with_stripe(owner, org_id: str) -> None:
    await owner.execute(
        "UPDATE subscriptions SET stripe_customer_id = 'cus_x', stripe_subscription_id = 'sub_x' "
        "WHERE org_id = $1::uuid",
        org_id,
    )


# ------------------------------------------------------------------------- the request
async def test_a_request_needs_the_owner_the_slug_and_their_password(
    client, signup_org, owner, cp
) -> None:
    a = await signup_org()
    slug = await slug_of(owner, a.org_id)
    assert (await request(client, a, slug, password=None)).status_code == 401
    assert (await request(client, a, slug, password=WRONG_PASSWORD)).status_code == 401
    assert (await request(client, a, "not-my-slug")).status_code == 422
    for role in ("admin", "member", "viewer"):
        token = issue_session(cp.session_secret, Session(a.user_id, a.org_id, role), ttl_seconds=60)
        res = await request(client, a, slug, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 403, role
    key = await a.make_key()
    assert (await request(client, a, slug, headers={"X-API-Key": key})).status_code in {401, 403}
    assert (await rows(owner, a.org_id))["erasure_requests"] == 0


async def test_a_request_only_schedules_it_and_tells_the_admins(
    client, signup_org, owner, cp
) -> None:
    recorder = Recorder()
    cp.extras["notifier"] = recorder
    a = await signup_org()
    res = await request(client, a, await slug_of(owner, a.org_id))
    assert res.status_code == 202
    body = res.json()
    assert body["scheduled"] is True
    when = datetime.fromisoformat(body["execute_after"])
    assert timedelta(days=13) < when - datetime.now(UTC) <= timedelta(days=14, minutes=1)
    assert (await rows(owner, a.org_id))["orgs"] == 1  # nothing is erased by the request
    assert [s[1] for s in recorder.sent] == ["Organisation erasure scheduled"]
    assert recorder.sent[0][0] == [a.email]
    audit = await owner.fetchval(
        "SELECT count(*) FROM audit_log "
        "WHERE org_id = $1::uuid AND action = 'org.erasure_requested'",
        a.org_id,
    )
    assert audit == 1
    again = await request(client, a, await slug_of(owner, a.org_id))
    assert again.status_code == 202 and len(recorder.sent) == 1  # a repeat changes nothing


async def test_the_database_itself_refuses_to_erase_before_the_grace_period_ends(
    client, signup_org, owner, db
) -> None:
    a = await signup_org()
    assert (await request(client, a, await slug_of(owner, a.org_id))).status_code == 202
    async with db.tenant(a.org_id) as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="grace period"):
            await conn.fetchval("SELECT cp_erase_org($1::uuid, gen_random_uuid(), 'x')", a.org_id)
    assert (await rows(owner, a.org_id))["orgs"] == 1


async def test_an_admin_can_cancel_and_the_status_is_visible(client, signup_org, owner, cp) -> None:
    recorder = Recorder()
    cp.extras["notifier"] = recorder
    a = await signup_org()
    await request(client, a, await slug_of(owner, a.org_id))
    status = (await client.get("/v1/org/erasure", headers=a.session)).json()
    assert status["scheduled"] is True
    admin = issue_session(cp.session_secret, Session(a.user_id, a.org_id, "admin"), ttl_seconds=60)
    res = await client.post("/v1/org/erase/cancel", headers={"Authorization": f"Bearer {admin}"})
    assert res.status_code == 200 and res.json()["cancelled"] is True
    assert (await client.get("/v1/org/erasure", headers=a.session)).json() == {"scheduled": False}
    assert recorder.sent[-1][1] == "Organisation erasure cancelled"
    totals = await erasure.run_due(cp, lambda: None)
    assert totals["erased"] == 0 and (await rows(owner, a.org_id))["orgs"] == 1


async def test_single_sign_on_accounts_need_a_recent_sign_in(
    client, signup_org, owner, cp, monkeypatch
) -> None:
    a = await signup_org()
    await owner.execute("UPDATE users SET password_hash = NULL WHERE id = $1::uuid", a.user_id)
    slug = await slug_of(owner, a.org_id)
    now = time.time()
    monkeypatch.setattr(security.time, "time", lambda: now - 3600)
    stale = issue_session(
        cp.session_secret, Session(a.user_id, a.org_id, "owner"), ttl_seconds=7200
    )
    monkeypatch.undo()
    headers = {"Authorization": f"Bearer {stale}"}
    res = await request(client, a, slug, password=None, headers=headers)
    assert res.status_code == 401 and res.json()["error"]["code"] == "reauthentication_required"
    assert (await request(client, a, slug, password=None)).status_code == 202  # a fresh sign-in


# ------------------------------------------------------------------------ the execution
async def test_the_scheduled_job_erases_every_row_and_file_and_leaves_evidence(
    client, signup_org, csv_factory, owner, cp, fake_stripe
) -> None:
    models = FakeModels()
    cp.gateway.private_models = models
    recorder = Recorder()
    cp.extras["notifier"] = recorder
    a = await signup_org()
    await a.make_key()
    await a.upload("MYCO", csv_factory())
    slug = await slug_of(owner, a.org_id)
    org_dir = cp.blobs.root / "orgs" / a.org_id
    assert any(org_dir.rglob("*"))

    totals = await schedule_and_run(client, a, owner, cp, fake_stripe)
    assert totals["erased"] == 1

    assert all(v == 0 for v in (await rows(owner, a.org_id)).values())
    assert not org_dir.exists()
    assert models.evicted == [a.org_id]
    receipt = await owner.fetchrow(
        "SELECT * FROM erasure_receipts WHERE org_id = $1::uuid", a.org_id
    )
    assert receipt["slug_hmac"] == erasure.slug_hmac(cp.settings, slug)
    assert receipt["slug_hmac"] != hashlib.sha256(slug.encode()).hexdigest()
    assert str(receipt["requested_by"]) == a.user_id and receipt["actor_role"] == "owner"
    assert receipt["blobs_erased_at"] is not None and receipt["audit_rows"] >= 1
    text = " ".join(str(v) for k, v in dict(receipt).items() if k != "requested_by")
    assert a.email not in text and slug not in text

    stones = Tombstones(erasure.tombstone_dir(cp.settings)).all()
    mine = [t for t in stones if t.org_id == a.org_id]
    assert len(mine) == 1 and mine[0].receipt_id == str(receipt["id"])
    dumped = json.dumps(mine[0].__dict__)
    assert a.email not in dumped and slug not in dumped
    assert recorder.sent[-1][1] == "Organisation erased"
    # an old session token can neither read the account nor recreate anything
    assert (await client.get("/v1/me", headers=a.session)).status_code >= 400
    stale = await client.post(
        "/v1/api-keys", json={"name": "x", "scopes": ["analytics"]}, headers=a.session
    )
    assert stale.status_code == 401
    assert all(v == 0 for v in (await rows(owner, a.org_id)).values())


async def test_other_organisations_are_untouched(
    client, signup_org, csv_factory, owner, cp, fake_stripe
) -> None:
    a, b = await signup_org(), await signup_org()
    await b.make_key()
    await b.upload("MYCO", csv_factory())
    before = await rows(owner, b.org_id)
    await schedule_and_run(client, a, owner, cp, fake_stripe)
    assert await rows(owner, b.org_id) == before
    assert (await client.get("/v1/me", headers=b.session)).status_code == 200
    assert any((cp.blobs.root / "orgs" / b.org_id).rglob("*"))


async def test_a_running_job_blocks_before_stripe_is_touched_and_unblocks_later(
    client, signup_org, owner, csv_factory, cp, fake_stripe
) -> None:
    a = await signup_org()
    uploaded = await a.upload("MYCO", csv_factory())
    await with_stripe(owner, a.org_id)
    job = await owner.fetchval(
        "INSERT INTO finetune_jobs (org_id, data_source_id, symbols, params, status, gpu_pool) "
        "VALUES ($1::uuid, $2::uuid, ARRAY['MYCO'], '{}'::jsonb, 'running', 'cpu') RETURNING id",
        a.org_id,
        uploaded["source_id"],
    )
    totals = await schedule_and_run(client, a, owner, cp, fake_stripe)
    assert totals["blocked"] == 1 and totals["erased"] == 0
    assert fake_stripe.canceled == [] and fake_stripe.redacted == []  # Stripe was not touched
    status = (await client.get("/v1/org/erasure", headers=a.session)).json()
    assert "fine-tune job is running" in status["blocked_reason"]
    await owner.execute("UPDATE finetune_jobs SET status = 'cancelled' WHERE id = $1", job)
    assert (await sweep(cp, fake_stripe))["erased"] == 1
    assert fake_stripe.canceled == ["sub_x"] and fake_stripe.redacted == ["cus_x"]


async def test_a_stripe_failure_pauses_the_erasure_and_a_retry_finishes_it(
    client, signup_org, owner, cp, fake_stripe
) -> None:
    a = await signup_org()
    await with_stripe(owner, a.org_id)
    fake_stripe.fail_cancel = True
    totals = await schedule_and_run(client, a, owner, cp, fake_stripe)
    assert totals["failed"] == 1 and (await rows(owner, a.org_id))["orgs"] == 1
    done = await owner.fetchval(
        "SELECT stripe_done_at FROM erasure_requests WHERE org_id = $1::uuid", a.org_id
    )
    assert done is None
    fake_stripe.fail_cancel = False
    assert (await sweep(cp, fake_stripe))["erased"] == 1
    assert fake_stripe.canceled == ["sub_x"]
    assert (await rows(owner, a.org_id))["orgs"] == 0


async def test_all_of_the_organisations_usage_is_reported_and_no_one_elses(
    client, signup_org, owner, cp, fake_stripe
) -> None:
    a, b = await signup_org(), await signup_org()
    await with_stripe(owner, a.org_id)
    await owner.execute(
        "UPDATE subscriptions SET stripe_customer_id = 'cus_other' WHERE org_id = $1::uuid",
        b.org_id,
    )
    for org_id, n in ((a.org_id, 1200), (b.org_id, 30)):  # 1200 is more than one 500-row batch
        await owner.execute(
            "INSERT INTO usage_events (org_id, kind, quantity, occurred_at, idempotency_key) "
            "SELECT $1::uuid, 'forecast_calls', 1, now(), 'k' || g FROM generate_series(1, $2) g",
            org_id,
            n,
        )
    await schedule_and_run(client, a, owner, cp, fake_stripe)
    assert len([u for u in fake_stripe.usage if u["customer"] == "cus_x"]) == 1200
    assert not [u for u in fake_stripe.usage if u["customer"] == "cus_other"]
    left = await owner.fetchval(
        "SELECT count(*) FROM usage_events WHERE org_id = $1::uuid AND reported_at IS NULL",
        b.org_id,
    )
    assert left == 30


async def test_failed_file_erasure_stays_pending_until_the_next_sweep(
    client, signup_org, csv_factory, owner, cp, fake_stripe, monkeypatch
) -> None:
    a = await signup_org()
    await a.upload("MYCO", csv_factory())
    real = cp.blobs.delete_org

    def broken(org_id: str) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(cp.blobs, "delete_org", broken)
    await schedule_and_run(client, a, owner, cp, fake_stripe)
    assert (await rows(owner, a.org_id))["orgs"] == 0
    assert await owner.fetchval("SELECT count(*) FROM cp_pending_erasures()") >= 1
    stats = await owner.fetchrow("SELECT * FROM cp_erasure_stats()")
    assert stats["pending_files"] >= 1
    monkeypatch.setattr(cp.blobs, "delete_org", real)
    assert (await sweep(cp, fake_stripe))["files_finished"] >= 1
    assert await owner.fetchval(
        "SELECT blobs_erased_at IS NOT NULL FROM erasure_receipts WHERE org_id = $1::uuid",
        a.org_id,
    )


async def test_a_missing_tombstone_is_written_on_the_next_sweep(
    client, signup_org, owner, cp, fake_stripe
) -> None:
    a = await signup_org()
    await schedule_and_run(client, a, owner, cp, fake_stripe)
    log = Tombstones(erasure.tombstone_dir(cp.settings))
    for path in log.directory.glob("*.json"):
        path.unlink()  # simulate a crash between the commit and the log write
    assert (await sweep(cp, fake_stripe))["tombstones"] >= 1
    assert any(t.org_id == a.org_id for t in log.all())


# --------------------------------------------------------------------- after a restore
async def test_replaying_the_log_erases_an_organisation_that_a_restore_brought_back(
    client, signup_org, owner, cp, fake_stripe, pg
) -> None:
    a = await signup_org()
    await schedule_and_run(client, a, owner, cp, fake_stripe)
    log = Tombstones(erasure.tombstone_dir(cp.settings))
    tomb = next(t for t in log.all() if t.org_id == a.org_id)
    # a restore from an older backup: the organisation is back and its receipt is gone
    await owner.execute("DELETE FROM erasure_receipts WHERE org_id = $1::uuid", a.org_id)
    await owner.execute(
        "INSERT INTO orgs (id, slug, name) VALUES ($1::uuid, 'restored-org', 'Restored')", a.org_id
    )
    assert await erasure.reapply(pg.owner, log, cp.blobs) >= 1
    assert (await rows(owner, a.org_id))["orgs"] == 0
    receipts = await owner.fetchval(
        "SELECT count(*) FROM erasure_receipts WHERE id = $1::uuid", tomb.receipt_id
    )
    assert receipts == 1
    assert await erasure.reapply(pg.owner, log, cp.blobs) == 0  # nothing left to erase


async def test_the_application_role_cannot_replay_or_read_receipts(db, signup_org) -> None:
    a = await signup_org()
    async with db.tenant(a.org_id) as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.fetch("SELECT * FROM erasure_receipts")
    async with db.anonymous() as conn:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.fetchval(
                "SELECT cp_reapply_erasure(gen_random_uuid(), gen_random_uuid(), 'x', NULL, 'o', "
                "now(), now())"
            )


async def test_the_database_function_refuses_the_wrong_tenant_context(
    db, signup_org, owner
) -> None:
    a, b = await signup_org(), await signup_org()
    async with db.tenant(b.org_id) as conn:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="own tenant context"):
            await conn.fetchval("SELECT cp_erase_org($1::uuid, gen_random_uuid(), 'x')", a.org_id)
    assert (await rows(owner, a.org_id))["orgs"] == 1


# --------------------------------------------------------------------------- the export
async def test_the_owner_can_export_everything_without_secrets(
    client, signup_org, csv_factory
) -> None:
    a = await signup_org()
    await a.make_key()
    await a.upload("MYCO", csv_factory())
    res = await client.get("/v1/org/export", headers=a.session)
    assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(res.content))
    names = set(archive.namelist())
    assert {"MANIFEST.json", "users.json", "audit_log.json", "api_keys.json"} <= names
    assert any(n.startswith("files/") and n.endswith("MYCO.csv") for n in names)
    blob = b"".join(archive.read(n) for n in names if n.endswith(".json"))
    assert a.email.encode() in blob
    for secret in (b"password_hash", b"secret_hash", b"ciphertext", b"wrapped_dek", b"stripe_"):
        assert secret not in blob
    manifest = json.loads(archive.read("MANIFEST.json"))
    assert manifest["disclaimer"] == "For research and risk analytics. Not investment advice."
    assert (await client.get("/v1/org/export", headers=a.session)).status_code == 429


async def test_only_the_owner_may_export(client, signup_org, cp) -> None:
    a = await signup_org()
    admin = issue_session(cp.session_secret, Session(a.user_id, a.org_id, "admin"), ttl_seconds=60)
    res = await client.get("/v1/org/export", headers={"Authorization": f"Bearer {admin}"})
    assert res.status_code == 403
    key = await a.make_key()
    res = await client.get("/v1/org/export", headers={"X-API-Key": key})
    assert res.status_code in {401, 403}


# ----------------------------------------------------------------------------- metrics
async def test_erasures_are_visible_as_counts_only(client, signup_org, owner, cp) -> None:
    a = await signup_org()
    await request(client, a, await slug_of(owner, a.org_id))
    cp.metrics._erasure_cache = (0.0, (0, 0, 0.0, 0))
    res = await client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"})
    assert res.status_code == 200
    assert "tycheon_erasures_scheduled" in res.text and "tycheon_erasure_files_pending" in res.text
    assert a.org_id not in res.text
