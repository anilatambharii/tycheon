"""The one door to Tycheon's analytics: authenticate, limit, meter, then a governed tool call.

Every data-plane request (REST or MCP) passes through :meth:`AnalyticsGateway.run`, in this
order, and nothing else calls the analytics:

1. the organisation's plan and entitlement are loaded (a lapsed subscription is the free plan);
2. the per-organisation rate limit is applied;
3. plan features are checked (calibration reports, MCP);
4. quota is *reserved* (or, for compute meters, headroom is checked);
5. the call goes through Tycheon's governance layer (``GovernedRuntime``): signed capability
   grant, policy, audit, bound to this tenant, this tenant's data source and this tenant's
   private models, with an ``as_of`` the server chose;
6. the reservation is committed on success and released on any failure.

Keelgate is never imported here: the control plane reaches it only through ``tycheon.governance``.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from tycheon.data.sample import sample_end
from tycheon.governance import AGENT_API, AGENT_MCP, GovernedRuntime
from tycheon.services import DataSource
from tycheon_cp import store
from tycheon_cp.datasources import source_directory
from tycheon_cp.errors import ApiProblem
from tycheon_cp.metering import FeatureNotIncludedError, QuotaExceededError
from tycheon_cp.ratelimit import RateLimitedError

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from tycheon.models.base import Forecaster
    from tycheon.services import ModelResolver
    from tycheon_cp.blob import BlobStore
    from tycheon_cp.db import Database
    from tycheon_cp.metering import Meter, Reservation
    from tycheon_cp.metrics import Metrics
    from tycheon_cp.plans import Plans
    from tycheon_cp.ratelimit import RateLimiter

#: tool -> (meter kind, plan feature it needs, is a compute meter measured in seconds)
TOOL_POLICY: dict[str, tuple[str | None, str | None, bool]] = {
    "forecast_distribution": ("forecast_calls", None, False),
    "calibration_report": ("calibration_reports", "calibration_reports", False),
    "portfolio_risk": ("risk_reports", None, False),
    "backtest_summary": ("backtest_compute_seconds", None, True),
    "risk_report": (None, None, False),  # reading back a report you already paid for
}

_STATUS = {
    "capability_denied": 403,
    "not_authorised": 403,
    "policy_denied": 403,
    "execution_mode_forbidden": 403,
    "side_effect_not_permitted": 403,
    "invalid_arguments": 422,
    "unknown_tool": 404,
    "budget_exceeded": 429,
    "tool_timeout": 504,
    "tool_failed": 400,
}


class ModelUnavailableError(Exception):
    """A private model name that this tenant cannot use (unknown, not theirs, not promoted)."""


class PrivateModels(Protocol):
    """Resolves a tenant's own fine-tuned or routed model; supplied by ``tycheon_ft``."""

    async def resolve(self, org_id: UUID, name: str, symbol: str | None) -> Forecaster: ...


class AnalyticsGateway:
    def __init__(
        self,
        *,
        db: Database,
        plans: Plans,
        meter: Meter,
        limiter: RateLimiter,
        runtime: GovernedRuntime,
        blobs: BlobStore,
        private_models: PrivateModels | None = None,
        metrics: Metrics | None = None,
        max_concurrency: int = 4,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db, self.plans, self.meter, self.limiter = db, plans, meter, limiter
        self.runtime, self.blobs, self.private_models = runtime, blobs, private_models
        self.metrics = metrics
        self._clock = clock or (lambda: datetime.now(UTC))
        self._limit = asyncio.Semaphore(max_concurrency)

    # ---------------------------------------------------------------- tenant context
    async def _tenant_data(self, org_id: UUID) -> tuple[DataSource | None, datetime]:
        """The tenant's default CSV source and the server's ``as_of`` for it."""
        source_id = await store.default_data_source(self.db, org_id)
        if source_id is None:
            return None, sample_end("SYN-GBM").to_pydatetime()
        path = source_directory(self.blobs, str(org_id), str(source_id))
        base = self.runtime.data
        return DataSource(
            kind="files", path=path, news=base.news, fundamentals=base.fundamentals
        ), self._clock()

    async def _resolver(self, org_id: UUID, arguments: dict[str, Any]) -> ModelResolver | None:
        name = arguments.get("model")
        if not isinstance(name, str) or not (name == "routed" or name.startswith("ft:")):
            return None
        if self.private_models is None:
            raise ApiProblem(400, "model_unavailable", "Private models are not available here.")
        symbol = arguments.get("symbol") if isinstance(arguments.get("symbol"), str) else None
        try:
            forecaster = await self.private_models.resolve(org_id, name, symbol)
        except ModelUnavailableError as exc:
            raise ApiProblem(400, "model_unavailable", str(exc)) from exc

        def resolve(requested: str) -> Forecaster:
            if requested != name:  # exactly the one model this call was authorised to use
                raise KeyError(requested)
            return forecaster

        return resolve  # type: ignore[return-value]

    def _refused(self, reason: str) -> None:
        if self.metrics is not None:
            self.metrics.refusals.labels(reason).inc()

    async def _admit(
        self, principal: store.Principal, tool: str, *, via_mcp: bool
    ) -> tuple[store.OrgContext, Reservation | None]:
        """Plan, rate limit, features and quota: everything that can refuse before any work."""
        kind, feature, is_compute = TOOL_POLICY[tool]
        org = await store.load_org(self.db, principal.org_id, self.plans)
        try:
            self.limiter.allow(str(org.org_id), org.plan.rate_limit_per_minute)
            if via_mcp:
                self.meter.require_feature(org.plan, "mcp")
            if feature:
                self.meter.require_feature(org.plan, feature)
        except RateLimitedError as exc:
            self._refused("rate_limited")
            raise ApiProblem(
                429,
                "rate_limited",
                "Too many requests; slow down.",
                headers={"Retry-After": str(max(1, round(exc.retry_after)))},
            ) from exc
        except FeatureNotIncludedError as exc:
            self._refused("plan_feature")
            raise ApiProblem(
                403, "plan_feature", f"Your {org.plan.name} plan does not include {exc.feature}."
            ) from exc
        reservation = None
        try:
            if kind and is_compute:
                await self.meter.ensure_headroom(org.org_id, org.plan, kind, override=org.override)
            elif kind:
                reservation = await self.meter.reserve(
                    org.org_id, org.plan, kind, override=org.override
                )
        except QuotaExceededError as exc:
            self._refused("quota_exceeded")
            raise ApiProblem(
                429, "quota_exceeded", f"Your monthly {exc.kind} quota is used up."
            ) from exc
        return org, reservation

    # --------------------------------------------------------------------------- run
    async def run(
        self,
        principal: store.Principal,
        tool: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
        via_mcp: bool = False,
    ) -> dict[str, Any]:
        if tool not in TOOL_POLICY:
            raise ApiProblem(404, "unknown_tool", "No such tool.")
        org, reservation = await self._admit(principal, tool, via_mcp=via_mcp)
        kind, _, is_compute = TOOL_POLICY[tool]

        started = time.perf_counter()
        try:
            data, as_of = await self._tenant_data(org.org_id)
            models = await self._resolver(org.org_id, arguments)
            async with self._limit:
                result = await self.runtime.call(
                    AGENT_MCP if via_mcp else AGENT_API,
                    tool,
                    arguments,
                    as_of=as_of,
                    tenant_id=str(org.org_id),
                    data=data,
                    models=models,
                )
        except BaseException:
            if reservation is not None:
                await self.meter.release(reservation)
            raise
        if not (result.ok and result.output is not None):
            if reservation is not None:
                await self.meter.release(reservation)
            code = result.error_code or result.status.lower()
            status = _STATUS.get(code, 400 if result.status == "ERROR" else 403)
            raise ApiProblem(
                status,
                code,
                result.message or "The request could not be completed.",
                details=list(result.policy_reasons),
            )
        if reservation is not None:
            await self.meter.commit(
                reservation, idempotency_key=idempotency_key, meta={"tool": tool}
            )
            if self.metrics is not None and kind:
                self.metrics.usage.labels(kind).inc()
        if self.metrics is not None and tool == "forecast_distribution":
            self.metrics.observe_forecast(result.output)
        elif kind and is_compute:
            seconds = max(1, round(time.perf_counter() - started))
            await self.meter.record_actual(
                org.org_id, kind, seconds, idempotency_key=idempotency_key, meta={"tool": tool}
            )
        return result.output
