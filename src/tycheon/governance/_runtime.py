"""The governed runtime: one object that assembles Keelgate's gateway for Tycheon.

It wires, from Keelgate's public contract only: the tool registry, a signing key and grant
verifier, the audit chain, the approval queue, the budget ledger, the idempotency store and the
policy engine, into one :class:`~keelgate.tools.ToolGateway`. Then it hands out *callers*: the
:class:`~tycheon.agents.protocols.ToolCaller` the agents use, which runs a named tool as a named
agent under that agent's own capability grant.

Least privilege is the point of :data:`AGENT_CAPABILITIES`: the news agent holds ``news:read`` and
nothing else; the trade proposer holds only ``trade:paper_execute`` (which the policy never
auto-approves); no agent holds a wildcard (Keelgate refuses wildcards).
"""

from __future__ import annotations

import secrets
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from keelgate.adapters.governed import GovernedToolset
from keelgate.approvals import (
    ApprovalError,
    ApprovalQueue,
    ApprovalTier,
    Approver,
    EvidenceSource,
)
from keelgate.audit import AuditLog, SqliteAuditStore
from keelgate.capabilities import GrantSigner, GrantVerifier, SqliteBudgetLedger, issue_grant
from keelgate.loop import SqliteCheckpointStore
from keelgate.tools import (
    CallContext,
    SqliteIdempotencyStore,
    ToolGateway,
    ToolRegistry,
)

from tycheon.agents.protocols import EvidenceRef, ToolResult
from tycheon.agents.specialists import (
    AGENT_BACKTEST,
    AGENT_COMPOSER,
    AGENT_FORECAST,
    AGENT_FUNDAMENTALS,
    AGENT_NEWS,
    AGENT_RISK,
    AGENT_TRADER,
)
from tycheon.governance._policy import TycheonPolicy, policy_context
from tycheon.governance._tools import build_tools
from tycheon.services import (
    DataSource,
    PaperBlotter,
    ToolContext,
    bind,
    build_sample_fundamentals,
    sample_news,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from keelgate.policy import PolicyEngine
    from keelgate.tools import ToolOutcome

AGENT_API = "api-reader"
AGENT_MCP = "mcp-agent"
_READ_CAPABILITIES = (
    "forecast:run",
    "calibration:run",
    "risk:compute",
    "backtest:run",
    "news:read",
    "fundamentals:read",
)

#: Each agent's capability grant. Exact capabilities only; no wildcards exist in Keelgate.
AGENT_CAPABILITIES: dict[str, tuple[str, ...]] = {
    AGENT_FORECAST: ("forecast:run", "calibration:run"),
    AGENT_RISK: ("risk:compute",),
    AGENT_BACKTEST: ("backtest:run",),
    AGENT_NEWS: ("news:read",),
    AGENT_FUNDAMENTALS: ("fundamentals:read",),
    AGENT_COMPOSER: ("report:write",),
    AGENT_TRADER: ("trade:paper_execute",),
    # the REST principal: read-only analytics
    AGENT_API: _READ_CAPABILITIES,
    # an MCP client: read-only analytics, plus proposals that always wait for a human
    AGENT_MCP: (*_READ_CAPABILITIES, "trade:paper_execute"),
}


class GovernanceError(Exception):
    """A governance operation that failed, with a stable machine-readable ``code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RuntimeConfig:
    state_dir: Path | None = None
    tenant_id: str = "default"
    data: DataSource | None = None
    limits: Mapping[str, Any] | None = None
    default_as_of: datetime | None = None
    approval_ttl: timedelta = timedelta(hours=24)
    grant_ttl: timedelta = timedelta(hours=12)
    max_cost_per_grant: float = 500.0
    #: Spend cap per grant window for the long-lived service principals (REST, MCP).
    service_max_cost: float = 5_000.0
    engine: PolicyEngine | None = None
    signer: GrantSigner | None = None


@dataclass(frozen=True)
class AuditSummary:
    ok: bool
    records_checked: int
    head_seq: int | None
    error: str | None


@dataclass(frozen=True)
class ApprovalSummary:
    request_id: str
    tenant_id: str
    agent_id: str
    tool: str
    tier: str
    status: str
    args: dict[str, Any]
    rationale: str
    evidence_uris: tuple[str, ...]
    flags: tuple[str, ...]
    policy_reasons: tuple[str, ...]
    signoff_code: str
    created_at: datetime
    expires_at: datetime


def _to_result(outcome: ToolOutcome) -> ToolResult:
    """Keelgate's outcome as the agents' result type. The one place tool output is unwrapped."""
    output = None
    if outcome.ok and outcome.output is not None:
        output = outcome.output.unwrap_untrusted().model_dump(mode="json")
    error, policy = outcome.error, outcome.policy
    return ToolResult(
        status=outcome.status.value,
        tool=outcome.tool,
        call_id=outcome.call_id,
        output=output,
        error_code=error.code.value if error else None,
        message=error.message if error else "",
        approval_id=outcome.approval_id,
        approval_tier=outcome.approval_tier.value if outcome.approval_tier else None,
        policy_effect=policy.effect.value if policy else None,
        policy_reasons=tuple(policy.reasons) if policy else (),
        audit_seq=outcome.audit_seq,
    )


@dataclass
class BoundToolset(GovernedToolset):
    """A :class:`GovernedToolset` that binds Tycheon's trusted context around each call.

    The tool bodies read ``as_of``, tenant and data from that context. It is set here, from the
    harness's own ``as_of_provider``, never from anything a client or model sent.
    """

    tenant_id: str = "default"
    data: DataSource | None = None
    as_of_provider: Any = None

    async def invoke(self, name: str, arguments: Mapping[str, Any]) -> ToolOutcome:
        ctx = ToolContext(
            as_of=self.as_of_provider(), tenant_id=self.tenant_id, data=self.data or DataSource()
        )
        with bind(ctx):
            return await super().invoke(name, arguments)


class GovernedRuntime:
    """Keelgate's gateway, audit chain, approvals and grants, assembled for Tycheon."""

    def __init__(self, config: RuntimeConfig = RuntimeConfig()) -> None:  # noqa: B008
        self.config = config
        self._owns_state = config.state_dir is None
        self.state_dir = (
            Path(tempfile.mkdtemp(prefix="tycheon-state-"))
            if config.state_dir is None
            else Path(config.state_dir)
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.signer = config.signer or GrantSigner.generate(
            key_id=f"tycheon-{secrets.token_hex(4)}"
        )
        self.data = config.data or DataSource(
            news=sample_news(),
            fundamentals=build_sample_fundamentals(self.state_dir / "fundamentals"),
        )
        self.blotter = PaperBlotter()
        self.report_dir = self.state_dir / "reports"

        self._audit_store = SqliteAuditStore(self.state_dir / "audit.sqlite")
        self.audit = AuditLog(self._audit_store)
        self.approvals = ApprovalQueue(
            self.state_dir / "approvals.sqlite", audit=self.audit, default_ttl=config.approval_ttl
        )
        self._ledger = SqliteBudgetLedger(self.state_dir / "budget.sqlite")
        self._idempotency = SqliteIdempotencyStore(self.state_dir / "idempotency.sqlite")
        self.checkpoints = SqliteCheckpointStore(self.state_dir / "checkpoints.sqlite")
        self.registry = ToolRegistry()
        for governed_tool in build_tools(blotter=self.blotter, report_dir=self.report_dir):
            self.registry.register(governed_tool)
        self.policy = config.engine or TycheonPolicy()
        self.gateway = ToolGateway(
            registry=self.registry,
            verifier=GrantVerifier({self.signer.key_id: self.signer.public_key_pem()}),
            engine=self.policy,
            audit=self.audit,
            approvals=self.approvals,
            ledger=self._ledger,
            idempotency=self._idempotency,
        )
        self._grants: dict[tuple[str, str], tuple[str, datetime]] = {}

    # ----------------------------------------------------------------- grants
    def grant_for(self, agent_id: str, tenant_id: str | None = None) -> str:
        """The signed grant for ``agent_id`` in a tenant.

        Grants expire and carry a spend cap, so a long-running process re-issues one once half
        its lifetime has passed: a new grant (new id, fresh budget window) before the old expires.
        """
        tenant = tenant_id or self.config.tenant_id
        if agent_id not in AGENT_CAPABILITIES:
            raise GovernanceError(
                "unknown_agent", f"no capability grant is defined for {agent_id!r}"
            )
        key = (agent_id, tenant)
        now = datetime.now(UTC)
        cached = self._grants.get(key)
        if cached is None or now - cached[1] > self.config.grant_ttl / 2:
            cap = (
                self.config.service_max_cost
                if agent_id in (AGENT_API, AGENT_MCP)
                else self.config.max_cost_per_grant
            )
            token = issue_grant(
                self.signer,
                agent_id=agent_id,
                tenant_id=tenant,
                capabilities=list(AGENT_CAPABILITIES[agent_id]),
                max_cost=cap,
                ttl=self.config.grant_ttl,
            ).token
            self._grants[key] = (token, now)
            return token
        return cached[0]

    def tool_names(self) -> list[str]:
        return list(self.registry.names())

    # ------------------------------------------------------------------ calls
    def policy_context(self, as_of: datetime, tenant_id: str | None = None) -> Any:
        tenant = tenant_id or self.config.tenant_id
        exposure = sum(o.notional for o in self.blotter.orders(tenant))
        return policy_context(
            as_of, limits=dict(self.config.limits or {}) or None, daily_notional=exposure
        )

    async def call(
        self,
        agent_id: str,
        tool: str,
        arguments: Mapping[str, Any],
        *,
        as_of: datetime,
        tenant_id: str | None = None,
        rationale: str = "",
        evidence: Sequence[EvidenceRef] = (),
        confidence: float | None = None,
        flags: Sequence[str] = (),
        approval_id: str | None = None,
    ) -> ToolResult:
        """Run one tool as ``agent_id``, through grant, policy, approval and audit."""
        tenant = tenant_id or self.config.tenant_id
        context = CallContext(
            tenant_id=tenant,
            policy_context=self.policy_context(as_of, tenant),
            rationale=rationale[:4000],
            sources=tuple(
                EvidenceSource(uri=e.uri[:2048], title=e.title[:300] if e.title else None)
                for e in evidence[:50]
            ),
            confidence=confidence,
            verifier_flags=tuple(flags),
            approval_id=approval_id,
        )
        with bind(ToolContext(as_of=as_of, tenant_id=tenant, data=self.data)):
            outcome = await self.gateway.call(
                tool_name=tool,
                arguments=arguments,
                grant_token=self.grant_for(agent_id, tenant),
                context=context,
            )
        return _to_result(outcome)

    def caller(self, as_of: datetime, tenant_id: str | None = None) -> _BoundCaller:
        """A :class:`~tycheon.agents.protocols.ToolCaller` fixed to one trusted ``as_of``."""
        return _BoundCaller(self, as_of, tenant_id or self.config.tenant_id)

    def toolset(
        self, agent_id: str, *, as_of: datetime | None = None, tenant_id: str | None = None
    ) -> BoundToolset:
        """A governed toolset for adapters (MCP): same gateway, grant and trusted context."""
        tenant = tenant_id or self.config.tenant_id
        fixed = as_of or self.config.default_as_of

        def provider() -> datetime:
            return fixed or datetime.now(UTC)

        return BoundToolset(
            gateway=self.gateway,
            registry=self.registry,
            grant_token=self.grant_for(agent_id, tenant),
            context_factory=lambda: CallContext(
                tenant_id=tenant, policy_context=self.policy_context(provider(), tenant)
            ),
            tenant_id=tenant,
            data=self.data,
            as_of_provider=provider,
        )

    # -------------------------------------------------------------- approvals
    def _summary(self, request: Any) -> ApprovalSummary:
        evidence = request.evidence
        return ApprovalSummary(
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            agent_id=request.agent_id,
            tool=request.tool_name,
            tier=request.tier.value,
            status=request.status.value,
            args=dict(evidence.args),
            rationale=evidence.rationale,
            evidence_uris=tuple(s.uri for s in evidence.sources),
            flags=tuple(evidence.verifier_flags),
            policy_reasons=tuple(evidence.policy_reasons),
            signoff_code=request.signoff_code,
            created_at=request.created_at,
            expires_at=request.expires_at,
        )

    def pending_approvals(self, tenant_id: str | None = None) -> list[ApprovalSummary]:
        return [
            self._summary(r)
            for r in self.approvals.list_pending(tenant_id or self.config.tenant_id)
        ]

    def approve(
        self,
        request_id: str,
        *,
        approver_id: str,
        tenant_id: str | None = None,
        max_tier: str = "ONE_CLICK",
        signoff_code: str | None = None,
        note: str | None = None,
    ) -> ApprovalSummary:
        """Record a human's approval. The approver's identity must come from authentication."""
        tenant = tenant_id or self.config.tenant_id
        approver = Approver(
            approver_id=approver_id, tenant_id=tenant, max_tier=ApprovalTier(max_tier)
        )
        try:
            return self._summary(
                self.approvals.approve(
                    tenant, request_id, approver, signoff_code=signoff_code, note=note
                )
            )
        except ApprovalError as exc:
            raise GovernanceError(type(exc).__name__, str(exc)) from exc

    def reject(
        self,
        request_id: str,
        *,
        approver_id: str,
        tenant_id: str | None = None,
        max_tier: str = "ONE_CLICK",
        note: str | None = None,
    ) -> ApprovalSummary:
        tenant = tenant_id or self.config.tenant_id
        approver = Approver(
            approver_id=approver_id, tenant_id=tenant, max_tier=ApprovalTier(max_tier)
        )
        try:
            return self._summary(self.approvals.reject(tenant, request_id, approver, note=note))
        except ApprovalError as exc:
            raise GovernanceError(type(exc).__name__, str(exc)) from exc

    # ------------------------------------------------------------------ audit
    def verify_audit(self, tenant_id: str | None = None) -> AuditSummary:
        """Verify the tenant's hash-chained audit log. A tampered chain fails."""
        result = self.audit.verify_chain(tenant_id or self.config.tenant_id)
        return AuditSummary(
            ok=result.ok,
            records_checked=result.records_checked,
            head_seq=result.head.seq if result.head else None,
            error=result.error,
        )

    def audit_events(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Audit records as plain dicts (event type, actor, sequence), oldest first."""
        return [
            {"seq": r.seq, "event": r.event_type, "actor": r.actor}
            for r in self.audit.records(tenant_id or self.config.tenant_id)
        ]

    # -------------------------------------------------------------- lifecycle
    def close(self) -> None:
        self.checkpoints.close()
        self._audit_store.close()
        self._idempotency.close()
        self._ledger.close()
        self.approvals.close()
        if self._owns_state:
            shutil.rmtree(self.state_dir, ignore_errors=True)


class _BoundCaller:
    """A ``ToolCaller`` over the runtime with a fixed trusted ``as_of`` and tenant."""

    def __init__(self, runtime: GovernedRuntime, as_of: datetime, tenant_id: str) -> None:
        self._runtime = runtime
        self._as_of = as_of
        self._tenant = tenant_id

    async def call(
        self,
        agent: str,
        tool: str,
        arguments: Mapping[str, Any],
        *,
        rationale: str = "",
        evidence: Sequence[EvidenceRef] = (),
        confidence: float | None = None,
        flags: Sequence[str] = (),
        approval_id: str | None = None,
    ) -> ToolResult:
        return await self._runtime.call(
            agent,
            tool,
            arguments,
            as_of=self._as_of,
            tenant_id=self._tenant,
            rationale=rationale,
            evidence=evidence,
            confidence=confidence,
            flags=flags,
            approval_id=approval_id,
        )
