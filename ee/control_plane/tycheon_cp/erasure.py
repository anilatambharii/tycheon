"""Erase an organisation and everything it owns, in two phases.

**Request** (owner, re-authenticated): schedules the erasure ``erasure_grace_days`` ahead,
records it in the audit log and tells the owners and admins. Any admin can cancel until it runs.

**Execute** (a scheduled job, never the request path): when the grace period has ended,

1. refuses while a fine-tune job is running, *before* touching Stripe;
2. Stripe: reports all of the organisation's usage, cancels the subscription with a final invoice
   and clears the customer's contact details. Each step is idempotent and the step is recorded, so
   a retry after a partial failure resumes instead of getting stuck. Finalised invoices keep their
   snapshot because tax and accounting law requires it;
3. one database call (``cp_erase_org``) deletes the organisation and every tenant row, and leaves a
   receipt with no name, email or slug. The database itself refuses before the grace period ends;
4. a tombstone is written to a durable log outside the database (see :mod:`tycheon_cp.tombstones`)
   so a restore from an older backup can be re-erased;
5. stored files are removed; if that fails the receipt stays pending and the next run retries.

Not covered here: backups age out on their own schedule, and other replicas drop a cached
fine-tuned model on its next use (within the revalidation interval) or at restart.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import asyncpg

from tycheon_cp.errors import ApiProblem
from tycheon_cp.notify import LogNotifier, Notifier
from tycheon_cp.ratelimit import RateLimitedError
from tycheon_cp.security import verify_password
from tycheon_cp.store import audit
from tycheon_cp.tombstones import Tombstone, Tombstones

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from uuid import UUID

    from tycheon_cp import store
    from tycheon_cp.app import ControlPlane
    from tycheon_cp.billing import Billing
    from tycheon_cp.blob import BlobStore
    from tycheon_cp.config import Settings
    from tycheon_cp.db import Database

log = logging.getLogger("tycheon_cp.erasure")

#: how recent a sign-in must be for an account that has no password to re-enter (SSO)
FRESH_SESSION_SECONDS = 15 * 60
#: password and slug attempts allowed per organisation per minute
ATTEMPTS_PER_MINUTE = 5

# stored in erasure_requests.last_error; fixed strings only, so nothing sensitive can end up there
BLOCKED_JOB = "a fine-tune job is running; cancel it and the erasure will proceed"
BLOCKED_BILLING_OFF = "billing is not configured but the organisation has a subscription"
BLOCKED_BILLING = "the billing step failed and will be retried"
BLOCKED_DB = "the database refused the erasure and it will be retried"


# ------------------------------------------------------------------------------ helpers
def receipt_key(settings: Settings) -> bytes:
    """The HMAC key for receipts: explicit, or derived from the session secret."""
    if settings.erasure_receipt_key:
        return settings.erasure_receipt_key.encode()
    return hmac.new(
        settings.session_secret.encode(), b"tycheon-erasure-receipt-v1", hashlib.sha256
    ).digest()


def slug_hmac(settings: Settings, slug: str) -> str:
    """A keyed hash of the slug: can prove an erasure to someone who knows the slug and the key,
    and cannot be reversed by trying a list of company names."""
    return hmac.new(receipt_key(settings), slug.encode(), hashlib.sha256).hexdigest()


def tombstone_dir(settings: Settings) -> Path:
    return settings.erasure_log_dir or (settings.storage_root / "_erasure-log")


def _notifier(cp: ControlPlane) -> Notifier:
    return cp.extras.get("notifier") or LogNotifier()


async def _recipients(conn: Any) -> list[str]:
    rows = await conn.fetch(
        "SELECT email FROM users WHERE role IN ('owner', 'admin') AND disabled_at IS NULL"
    )
    return [r["email"] for r in rows]


async def _tell(cp: ControlPlane, recipients: list[str], subject: str, body: str) -> None:
    if not recipients:
        return
    try:
        await _notifier(cp).send(recipients, subject, body)
    except Exception as exc:
        log.error("erasure notice failed (%s)", type(exc).__name__)  # noqa: TRY400


# ---------------------------------------------------------------------------- phase 1
async def status(db: Database, org_id: UUID) -> dict[str, Any]:
    async with db.tenant(org_id) as conn:
        row = await conn.fetchrow(
            "SELECT requested_at, execute_after, attempts, last_error FROM erasure_requests"
        )
    if row is None:
        return {"scheduled": False}
    return {
        "scheduled": True,
        "requested_at": row["requested_at"],
        "execute_after": row["execute_after"],
        "attempts": row["attempts"],
        "blocked_reason": row["last_error"],
    }


async def request_erasure(
    cp: ControlPlane,
    principal: store.Principal,
    *,
    confirm_slug: str,
    password: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Schedule the erasure. Needs the owner's slug confirmation and a fresh proof of identity."""
    org_id = principal.org_id
    try:
        cp.limiter.allow(f"erase:{org_id}", ATTEMPTS_PER_MINUTE)
    except RateLimitedError as exc:
        raise ApiProblem(429, "rate_limited", "Too many attempts; wait a minute.") from exc
    moment = now or datetime.now(UTC)
    async with cp.db.tenant(org_id) as conn:
        slug = await conn.fetchval("SELECT slug FROM orgs WHERE id = $1", org_id)
        password_hash = await conn.fetchval(
            "SELECT password_hash FROM users WHERE id = $1", principal.user_id
        )
    if slug is None:
        raise ApiProblem(404, "not_found", "No such organisation.")
    if not hmac.compare_digest(confirm_slug.encode(), slug.encode()):
        raise ApiProblem(
            422, "confirmation_mismatch", "confirm_slug must equal the organisation's slug."
        )
    _require_fresh_identity(password_hash, password, principal, moment)

    execute_after = moment + timedelta(days=cp.settings.erasure_grace_days)
    async with cp.db.tenant(org_id) as conn:
        inserted = await conn.fetchrow(
            "INSERT INTO erasure_requests (org_id, requested_by, requested_by_role, requested_at, "
            "execute_after) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (org_id) DO NOTHING "
            "RETURNING execute_after",
            org_id,
            principal.user_id,
            principal.role,
            moment,
            execute_after,
        )
        if inserted is not None:
            await audit(
                conn,
                principal,
                "org.erasure_requested",
                None,
                {"execute_after": execute_after.isoformat()},
            )
            recipients = await _recipients(conn)
        else:
            recipients = []
    current = await status(cp.db, org_id)
    if inserted is not None:
        when = execute_after.date().isoformat()
        await _tell(
            cp,
            recipients,
            "Organisation erasure scheduled",
            f"The organisation will be permanently erased on or after {when}. An admin can cancel "
            "until then (POST /v1/org/erase/cancel, or the dashboard). If you did not expect this, "
            "cancel it now and rotate your credentials.",
        )
    return current


def _require_fresh_identity(
    password_hash: str | None, password: str | None, principal: store.Principal, now: datetime
) -> None:
    if password_hash:
        if not password or not verify_password(password, password_hash):
            raise ApiProblem(401, "reauthentication_required", "Confirm your password to continue.")
        return
    # no password (single sign-on): require a recent sign-in instead
    age = int(now.timestamp()) - principal.issued_at
    if principal.issued_at <= 0 or age > FRESH_SESSION_SECONDS:
        raise ApiProblem(
            401, "reauthentication_required", "Sign in again to continue with this action."
        )


async def cancel_erasure(cp: ControlPlane, principal: store.Principal) -> dict[str, Any]:
    async with cp.db.tenant(principal.org_id) as conn:
        done = await conn.execute("DELETE FROM erasure_requests")
        cancelled = done != "DELETE 0"
        if cancelled:
            await audit(conn, principal, "org.erasure_cancelled", None, {})
            recipients = await _recipients(conn)
        else:
            recipients = []
    if cancelled:
        await _tell(
            cp,
            recipients,
            "Organisation erasure cancelled",
            "The scheduled erasure of the organisation was cancelled.",
        )
    return {"scheduled": False, "cancelled": cancelled}


# ---------------------------------------------------------------------------- phase 2
async def run_due(cp: ControlPlane, billing: Callable[[], Billing | None]) -> dict[str, int]:
    """Carry out every erasure whose grace period has ended, then tidy up. Run on a schedule."""
    totals = {"erased": 0, "blocked": 0, "failed": 0, "files_finished": 0, "tombstones": 0}
    async with cp.db.anonymous() as conn:
        due = [r["org_id"] for r in await conn.fetch("SELECT org_id FROM cp_due_erasures()")]
    for org_id in due:
        outcome = await execute_one(cp, org_id, billing)
        totals[outcome] += 1
    totals["files_finished"] = await finish_pending(cp)
    totals["tombstones"] = await backfill_tombstones(cp)
    return totals


async def execute_one(cp: ControlPlane, org_id: UUID, billing: Callable[[], Billing | None]) -> str:
    """Erase one organisation. Returns ``erased``, ``blocked`` or ``failed``."""
    async with cp.db.tenant(org_id) as conn:
        request = await conn.fetchrow("SELECT * FROM erasure_requests")
        slug = await conn.fetchval("SELECT slug FROM orgs WHERE id = $1", org_id)
        running = await conn.fetchval("SELECT count(*) FROM finetune_jobs WHERE status = 'running'")
        sub = await conn.fetchrow(
            "SELECT stripe_customer_id, stripe_subscription_id FROM subscriptions"
        )
        recipients = await _recipients(conn)
    if request is None or slug is None:
        return "failed"
    if running:
        await _note(cp.db, org_id, BLOCKED_JOB)
        return "blocked"

    customer = sub["stripe_customer_id"] if sub else None
    subscription = sub["stripe_subscription_id"] if sub else None
    if (customer or subscription) and request["stripe_done_at"] is None:
        service = billing()
        if service is None:
            await _note(cp.db, org_id, BLOCKED_BILLING_OFF)
            return "blocked"
        try:
            await service.drain_org_usage(org_id)
            if subscription:
                await asyncio.to_thread(service.api.cancel_subscription, subscription)
            if customer:
                await asyncio.to_thread(service.api.redact_customer, customer)
        except Exception as exc:
            log.error("erasure paused: billing step failed (%s)", type(exc).__name__)  # noqa: TRY400
            await _note(cp.db, org_id, BLOCKED_BILLING)
            return "failed"
        async with cp.db.tenant(org_id) as conn:
            await conn.execute("UPDATE erasure_requests SET stripe_done_at = now()")

    receipt = uuid.uuid4()
    try:
        async with cp.db.tenant(org_id) as conn:
            await conn.fetchval(
                "SELECT cp_erase_org($1, $2, $3)", org_id, receipt, slug_hmac(cp.settings, slug)
            )
    except asyncpg.exceptions.RaiseError as exc:
        log.error("erasure refused by the database: %s", exc.message)  # noqa: TRY400
        await _note(cp.db, org_id, BLOCKED_DB)
        return "blocked"

    await backfill_tombstones(cp)
    evict = getattr(getattr(cp.gateway, "private_models", None), "evict_org", None)
    if callable(evict):
        evict(org_id)
    await _erase_files(cp, receipt, org_id)
    await _tell(
        cp,
        recipients,
        "Organisation erased",
        "The organisation and its data have been permanently erased. Backups age out on the "
        "published retention schedule.",
    )
    return "erased"


async def _note(db: Database, org_id: UUID, reason: str) -> None:
    async with db.tenant(org_id) as conn:
        await conn.execute(
            "UPDATE erasure_requests SET attempts = attempts + 1, last_error = $1", reason
        )


async def _erase_files(cp: ControlPlane, receipt: UUID, org_id: UUID) -> bool:
    try:
        await asyncio.to_thread(cp.blobs.delete_org, str(org_id))
        async with cp.db.anonymous() as conn:
            await conn.fetchval("SELECT cp_mark_blobs_erased($1)", receipt)
    except Exception as exc:
        log.error("file erasure pending for receipt %s (%s)", receipt, type(exc).__name__)  # noqa: TRY400
        return False
    return True


async def finish_pending(cp: ControlPlane) -> int:
    """Complete file erasure for receipts that are still pending. Returns how many finished."""
    async with cp.db.anonymous() as conn:
        pending = await conn.fetch("SELECT receipt_id, org_id FROM cp_pending_erasures()")
    done = 0
    for row in pending:
        if await _erase_files(cp, row["receipt_id"], row["org_id"]):
            done += 1
    return done


async def backfill_tombstones(cp: ControlPlane) -> int:
    """Write the durable tombstone for every receipt that does not have one yet.

    A tombstone is written only after the database has committed the erasure, never before, so it
    can never describe an organisation that was not erased. A crash between the two is repaired
    here on the next run.
    """
    log_dir = Tombstones(tombstone_dir(cp.settings))
    have = {t.receipt_id for t in log_dir.all()}
    async with cp.db.anonymous() as conn:
        rows = await conn.fetch("SELECT * FROM cp_list_receipts()")
    written = 0
    for r in rows:
        if str(r["id"]) in have:
            continue
        log_dir.write(
            Tombstone(
                receipt_id=str(r["id"]),
                org_id=str(r["org_id"]),
                slug_hmac=r["slug_hmac"],
                requested_by=str(r["requested_by"]) if r["requested_by"] else None,
                actor_role=r["actor_role"],
                requested_at=r["requested_at"].isoformat(),
                erased_at=r["db_erased_at"].isoformat(),
            )
        )
        written += 1
    return written


# ----------------------------------------------------------------------- after a restore
async def reapply(owner_url: str, tombstones: Tombstones, blobs: BlobStore) -> int:
    """Re-erase every organisation in the tombstone log. Run after restoring a backup.

    Connects as the database owner: the function it calls is deliberately not available to the
    application role. Returns how many organisations had come back and were erased again.
    """
    conn = await asyncpg.connect(owner_url)
    try:
        again = 0
        for t in tombstones.all():
            came_back = await conn.fetchval(
                "SELECT cp_reapply_erasure($1::uuid, $2::uuid, $3, $4::uuid, $5, $6, $7)",
                t.org_id,
                t.receipt_id,
                t.slug_hmac,
                t.requested_by,
                t.actor_role,
                t.requested_at_dt(),
                t.erased_at_dt(),
            )
            blobs.delete_org(t.org_id)
            again += 1 if came_back else 0
        return again
    finally:
        await conn.close()


__all__ = [
    "cancel_erasure",
    "execute_one",
    "reapply",
    "request_erasure",
    "run_due",
    "slug_hmac",
    "status",
]
