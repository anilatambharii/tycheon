"""Erase an organisation and everything it owns (right to erasure).

Order matters, and each step is chosen so a failure leaves the account safe rather than half
erased:

1. Stripe first: report committed usage and cancel the subscription. If either fails nothing
   local is touched, so the customer is never left billed for an account that no longer exists.
   The Stripe *customer* record is kept: invoices and tax records have their own retention rules.
2. One database call removes every tenant row (``cp_erase_org``) and writes a receipt holding no
   personal data. It refuses while a fine-tune job is running.
3. Stored files (CSV uploads, model artifacts) are removed by prefix. If that fails the receipt
   stays *pending* and ``finish_pending`` (CLI ``erase-pending``) completes it; the rows are
   already gone, so nothing is reachable through the API in the meantime.

Not erased here: backups age out on their own schedule (see the erasure runbook), and
already-issued session tokens stop working because the organisation no longer exists.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import asyncpg

from tycheon_cp.blob import org_key
from tycheon_cp.errors import ApiProblem

if TYPE_CHECKING:
    from uuid import UUID

    from tycheon_cp import store
    from tycheon_cp.billing import Billing
    from tycheon_cp.blob import BlobStore
    from tycheon_cp.db import Database

log = logging.getLogger("tycheon_cp.erasure")


async def erase_org(
    db: Database,
    blobs: BlobStore,
    billing: Billing | None,
    principal: store.Principal,
    *,
    confirm_slug: str,
) -> dict[str, Any]:
    """Erase ``principal``'s organisation. The caller has already required the owner role."""
    org_id = principal.org_id
    async with db.tenant(org_id) as conn:
        slug = await conn.fetchval("SELECT slug FROM orgs WHERE id = $1", org_id)
        subscription = await conn.fetchval("SELECT stripe_subscription_id FROM subscriptions")
    if slug is None:
        raise ApiProblem(404, "not_found", "No such organisation.")
    if confirm_slug != slug:
        raise ApiProblem(
            422, "confirmation_mismatch", "confirm_slug must equal the organisation's slug."
        )

    if subscription is not None:
        if billing is None:
            raise ApiProblem(503, "billing_unavailable", "Billing is not configured.")
        try:
            await billing.report_usage()
            await asyncio.to_thread(billing.api.cancel_subscription, subscription)
        except Exception as exc:
            log.error("erasure aborted: stripe step failed (%s)", type(exc).__name__)  # noqa: TRY400
            raise ApiProblem(
                502, "billing_failed", "Could not cancel the subscription; nothing was erased."
            ) from exc

    try:
        async with db.tenant(org_id) as conn:
            receipt = await conn.fetchval("SELECT cp_erase_org($1, $2)", org_id, principal.role)
    except asyncpg.exceptions.RaiseError as exc:
        raise ApiProblem(
            409, "conflict", exc.message or "Could not erase the organisation."
        ) from exc

    blobs_erased = await _erase_blobs(db, blobs, receipt, org_id)
    return {"receipt_id": str(receipt), "files_erased": blobs_erased}


async def _erase_blobs(db: Database, blobs: BlobStore, receipt: UUID, org_id: UUID) -> bool:
    try:
        await asyncio.to_thread(blobs.delete_prefix, org_key(str(org_id)))
        async with db.anonymous() as conn:
            await conn.fetchval("SELECT cp_mark_blobs_erased($1)", receipt)
    except Exception as exc:
        log.error("file erasure pending for receipt %s (%s)", receipt, type(exc).__name__)  # noqa: TRY400
        return False
    return True


async def finish_pending(db: Database, blobs: BlobStore) -> int:
    """Complete file erasure for receipts that are still pending. Returns how many finished."""
    async with db.anonymous() as conn:
        pending = await conn.fetch("SELECT receipt_id, org_id FROM cp_pending_erasures()")
    done = 0
    for row in pending:
        if await _erase_blobs(db, blobs, row["receipt_id"], row["org_id"]):
            done += 1
    return done
