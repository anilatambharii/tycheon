"""Usage metering and quota enforcement.

A metered call is *reserved* before it runs, *committed* when it succeeds and *released* when it
fails, so a failed call is never billed and two concurrent calls can never both take the last unit
of quota: the reservation is a single atomic ``INSERT ... ON CONFLICT DO UPDATE ... WHERE used + q
<= limit`` on the counter row, which Postgres serialises per row.

The committed event carries an idempotency key. Retrying the same request with the same key
records one event and gives back the duplicate reservation, so the counter always equals the sum
of recorded events (the invariant the tests check).

Compute meters (backtest seconds, GPU seconds) cannot be known before the work runs: they are
checked for *remaining headroom* up front and recorded exactly afterwards, so one job can
overshoot its limit by its own duration. That is deliberate and documented.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from tycheon_cp.db import Database
    from tycheon_cp.plans import Plan, Plans

_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")

_RESERVE = """
WITH ins AS (
  INSERT INTO usage_counters (org_id, period, kind, used)
  SELECT $1::uuid, $2::date, $3::text, $4::bigint WHERE $4::bigint <= $5::bigint
  ON CONFLICT (org_id, period, kind) DO UPDATE SET used = usage_counters.used + $4::bigint
    WHERE usage_counters.used + $4::bigint <= $5::bigint
  RETURNING used
) SELECT used FROM ins
"""


class QuotaExceededError(Exception):
    """The organisation has no quota left for this meter this month."""

    def __init__(self, kind: str, limit: int, used: int) -> None:
        super().__init__(f"monthly {kind} quota of {limit} is used up")
        self.kind, self.limit, self.used = kind, limit, used


class FeatureNotIncludedError(Exception):
    """The organisation's plan does not include this feature."""

    def __init__(self, feature: str, plan: str) -> None:
        super().__init__(f"the {plan} plan does not include {feature}")
        self.feature, self.plan = feature, plan


@dataclass(frozen=True)
class Reservation:
    org_id: UUID
    kind: str
    quantity: int
    period: date


def clean_idempotency_key(value: str | None) -> str:
    """A caller-supplied key if it is well formed, otherwise a fresh one (never trust the shape)."""
    if value and _IDEMPOTENCY.fullmatch(value):
        return value
    return uuid.uuid4().hex


def current_period(now: datetime | None = None) -> date:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    return date(moment.year, moment.month, 1)


class Meter:
    """Reserve, commit, release and read usage. Every operation is its own transaction."""

    def __init__(
        self, db: Database, plans: Plans, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._db = db
        self._plans = plans
        self._clock = clock or (lambda: datetime.now(UTC))

    def period(self) -> date:
        return current_period(self._clock())

    def require_feature(self, plan: Plan, feature: str) -> None:
        if not plan.has(feature):
            raise FeatureNotIncludedError(feature, plan.key)

    async def reserve(
        self,
        org_id: UUID,
        plan: Plan,
        kind: str,
        quantity: int = 1,
        *,
        override: dict[str, Any] | None = None,
    ) -> Reservation:
        limit = plan.limit(kind, override)
        period = self.period()
        async with self._db.tenant(org_id) as conn:
            row = await conn.fetchrow(_RESERVE, org_id, period, kind, quantity, limit)
            if row is None:
                used = await conn.fetchval(
                    "SELECT used FROM usage_counters WHERE org_id=$1 AND period=$2 AND kind=$3",
                    org_id,
                    period,
                    kind,
                )
                raise QuotaExceededError(kind, limit, int(used or 0))
        return Reservation(org_id, kind, quantity, period)

    async def commit(
        self,
        reservation: Reservation,
        *,
        idempotency_key: str,
        meta: dict[str, Any] | None = None,
    ) -> bool:
        """Record the event. ``False`` means this key was already recorded (and was refunded)."""
        async with self._db.tenant(reservation.org_id) as conn:
            row = await conn.fetchrow(
                "INSERT INTO usage_events "
                "(org_id, kind, quantity, occurred_at, idempotency_key, meta) "
                "VALUES ($1, $2, $3, $4, $5, $6::jsonb) "
                "ON CONFLICT (org_id, idempotency_key) DO NOTHING RETURNING id",
                reservation.org_id,
                reservation.kind,
                reservation.quantity,
                self._clock(),
                idempotency_key,
                json.dumps(meta or {}),
            )
            if row is None:
                await self._decrement(conn, reservation)
                return False
        return True

    async def release(self, reservation: Reservation) -> None:
        async with self._db.tenant(reservation.org_id) as conn:
            await self._decrement(conn, reservation)

    @staticmethod
    async def _decrement(conn: Any, reservation: Reservation) -> None:
        await conn.execute(
            "UPDATE usage_counters SET used = GREATEST(used - $4, 0) "
            "WHERE org_id=$1 AND period=$2 AND kind=$3",
            reservation.org_id,
            reservation.period,
            reservation.kind,
            reservation.quantity,
        )

    async def ensure_headroom(
        self, org_id: UUID, plan: Plan, kind: str, *, override: dict[str, Any] | None = None
    ) -> None:
        """For compute meters: refuse to start when the month's limit is already reached."""
        limit = plan.limit(kind, override)
        async with self._db.tenant(org_id) as conn:
            used = await conn.fetchval(
                "SELECT used FROM usage_counters WHERE org_id=$1 AND period=$2 AND kind=$3",
                org_id,
                self.period(),
                kind,
            )
        if int(used or 0) >= limit:
            raise QuotaExceededError(kind, limit, int(used or 0))

    async def record_actual(
        self,
        org_id: UUID,
        kind: str,
        quantity: int,
        *,
        idempotency_key: str,
        meta: dict[str, Any] | None = None,
    ) -> bool:
        """Record compute that already happened (no limit check: see the module docstring)."""
        if quantity <= 0:
            return False
        period = self.period()
        async with self._db.tenant(org_id) as conn:
            row = await conn.fetchrow(
                "INSERT INTO usage_events "
                "(org_id, kind, quantity, occurred_at, idempotency_key, meta) "
                "VALUES ($1, $2, $3, $4, $5, $6::jsonb) "
                "ON CONFLICT (org_id, idempotency_key) DO NOTHING RETURNING id",
                org_id,
                kind,
                quantity,
                self._clock(),
                idempotency_key,
                json.dumps(meta or {}),
            )
            if row is None:
                return False
            await conn.execute(
                "INSERT INTO usage_counters (org_id, period, kind, used) VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (org_id, period, kind) DO UPDATE SET used = usage_counters.used + $4",
                org_id,
                period,
                kind,
                quantity,
            )
        return True

    async def summary(
        self, org_id: UUID, plan: Plan, *, override: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """This month's usage against the plan's limits, one row per meter."""
        async with self._db.tenant(org_id) as conn:
            rows = await conn.fetch(
                "SELECT kind, used FROM usage_counters WHERE org_id=$1 AND period=$2",
                org_id,
                self.period(),
            )
        used = {r["kind"]: int(r["used"]) for r in rows}
        return [
            {
                "kind": kind,
                "unit": unit,
                "used": used.get(kind, 0),
                "limit": plan.limit(kind, override),
                "period": self.period().isoformat(),
            }
            for kind, unit in self._plans.meters.items()
        ]
