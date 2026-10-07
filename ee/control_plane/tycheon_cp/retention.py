"""Retention per plan: expired operational data is deleted, billing and live models are not.

What is purged, per organisation, older than its plan's ``retention_days``:

* usage events that have already been reported to Stripe (an unreported event is billing data and
  is kept until it has been sent);
* audit-log entries (through one narrow database function; never newer than 30 days);
* finished fine-tune jobs, and rejected or archived models with their stored artifacts.

Promoted models, data files, counters and credentials are not purged by retention: they are the
customer's assets and are removed by the customer (or when the organisation is deleted).

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from tycheon_cp import store

if TYPE_CHECKING:
    from tycheon_cp.blob import BlobStore
    from tycheon_cp.db import Database
    from tycheon_cp.plans import Plans

MIN_RETENTION_DAYS = 30


async def purge(
    db: Database, plans: Plans, blobs: BlobStore, *, now: datetime | None = None
) -> dict[str, int]:
    """Apply every organisation's retention. Returns what was deleted, by kind."""
    moment = now or datetime.now(UTC)
    totals = {"usage_events": 0, "audit_log": 0, "finetune_jobs": 0, "models": 0}
    async with db.anonymous() as conn:
        orgs = [r["org_id"] for r in await conn.fetch("SELECT org_id FROM cp_list_orgs()")]
    for org_id in orgs:
        org = await store.load_org(db, org_id, plans)
        days = max(org.plan.retention_days, MIN_RETENTION_DAYS)
        cutoff = moment - timedelta(days=days)
        async with db.tenant(org_id) as conn:
            totals["usage_events"] += _count(await conn.execute(_USAGE_SQL, cutoff))
            stale = await conn.fetch(_STALE_MODELS_SQL, cutoff)
            if stale:
                ids = [m["id"] for m in stale]
                await conn.execute("DELETE FROM models WHERE id = ANY($1::uuid[])", ids)
            totals["models"] += len(stale)
            totals["finetune_jobs"] += _count(await conn.execute(_JOBS_SQL, cutoff))
        for model in stale:
            blobs.delete_prefix(model["artifact_key"])
        async with db.anonymous() as conn:
            totals["audit_log"] += int(
                await conn.fetchval("SELECT cp_purge_audit($1, $2)", org_id, days)
            )
    return totals


def _count(status: str) -> int:
    """Rows affected, from an asyncpg command tag such as ``DELETE 3``."""
    return int(status.rsplit(maxsplit=1)[-1])


# an event that has not been sent to Stripe is billing data: it is kept until it has been sent
_USAGE_SQL = (
    "DELETE FROM usage_events WHERE occurred_at < $1 AND (reported_at IS NOT NULL OR NOT EXISTS "
    "(SELECT 1 FROM subscriptions WHERE stripe_customer_id IS NOT NULL))"
)
_STALE_MODELS_SQL = (
    "SELECT id, artifact_key FROM models "
    "WHERE status IN ('rejected', 'archived') AND created_at < $1"
)
_JOBS_SQL = (
    "DELETE FROM finetune_jobs "
    "WHERE status IN ('succeeded', 'failed', 'cancelled') AND finished_at < $1"
)
