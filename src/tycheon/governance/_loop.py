"""The draft -> verify -> revise cycle, run on Keelgate's durable, budgeted loop.

Keelgate's ``Loop`` already provides what this cycle needs: a planning role that proposes, a
verifier role that can accept or send back for revision, a revision limit, token and time
budgets, checkpoints, and audit records. Tycheon plugs its own roles in:

* the *planner role* is a :class:`_DraftPlanner` that asks the report writer for a draft (and
  re-drafts with the verifier's reasons);
* the *verifier role* is :class:`_VerifierAdapter` over Tycheon's independent
  :class:`~tycheon.agents.verifier.ReportVerifier`.

A draft the verifier never accepts ends the run with ``verifier_rejections``; the workflow then
withholds the report. Neither role can execute anything: only the gateway can.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from keelgate.llm import Usage
from keelgate.loop import (
    Loop,
    Plan,
    PlanRequest,
    StopConditions,
    Verdict,
    VerdictDecision,
    VerifyRequest,
)

from tycheon.agents.composer import VerdictTrace
from tycheon.agents.specialists import AGENT_COMPOSER
from tycheon.agents.workflow import ComposeOutcome

if TYPE_CHECKING:
    from datetime import datetime

    from tycheon.agents.composer import DraftWriter
    from tycheon.agents.models import EvidenceBook
    from tycheon.agents.plan import ResearchPlan
    from tycheon.agents.verifier import ReportVerifier
    from tycheon.governance._runtime import GovernedRuntime


class _Shared:
    """State the two roles share within one run (same process, same event loop)."""

    def __init__(self) -> None:
        self.feedback: list[str] = []
        self.drafts: list[str] = []


class _DraftPlanner:
    """Keelgate's planner role, played by the report writer."""

    def __init__(
        self, writer: DraftWriter, plan: ResearchPlan, book: EvidenceBook, shared: _Shared
    ) -> None:
        self._writer = writer
        self._plan = plan
        self._book = book
        self._shared = shared

    async def plan(self, request: PlanRequest) -> Plan:  # noqa: ARG002 - the loop's own context is unused
        result = await self._writer.draft(self._plan, self._book, tuple(self._shared.feedback))
        text = result.text.strip() or "(the model returned an empty draft)"
        self._shared.drafts.append(text)
        return Plan(
            final_answer=text,
            usage=Usage(input_tokens=result.input_tokens, output_tokens=result.output_tokens),
            model="tycheon-writer",
            text=text,
        )


class _VerifierAdapter:
    """Keelgate's verifier role, played by Tycheon's independent verifier."""

    def __init__(
        self, verifier: ReportVerifier, plan: ResearchPlan, book: EvidenceBook, shared: _Shared
    ) -> None:
        self._verifier = verifier
        self._plan = plan
        self._book = book
        self._shared = shared

    async def verify(self, request: VerifyRequest) -> Verdict:
        verdict = self._verifier.verify(request.final_answer or "", self._book, self._plan)
        if verdict.accepted:
            self._shared.feedback = []
            return Verdict.accept(*verdict.reasons)
        self._shared.feedback = list(verdict.reasons)
        decision = (
            VerdictDecision.REJECT if verdict.decision == "REJECT" else VerdictDecision.REVISE
        )
        return Verdict(decision, tuple(verdict.reasons), tuple(verdict.flags))


class KeelgateComposeRunner:
    """Implements :class:`~tycheon.agents.workflow.ComposeRunner` on Keelgate's ``Loop``."""

    def __init__(
        self, runtime: GovernedRuntime, *, as_of: datetime, tenant_id: str | None = None
    ) -> None:
        self._runtime = runtime
        self._as_of = as_of
        self._tenant = tenant_id or runtime.config.tenant_id

    async def compose(
        self,
        *,
        plan: ResearchPlan,
        book: EvidenceBook,
        writer: DraftWriter,
        verifier: ReportVerifier,
    ) -> ComposeOutcome:
        rt, tenant = self._runtime, self._tenant
        shared = _Shared()
        revisions = plan.budget.max_revisions
        loop = Loop(
            gateway=rt.gateway,
            registry=rt.registry,
            planner=_DraftPlanner(writer, plan, book, shared),
            checkpoints=rt.checkpoints,
            grant_token=rt.grant_for(AGENT_COMPOSER, tenant),
            policy_context=lambda as_of: rt.policy_context(as_of, tenant),
            verifier=_VerifierAdapter(verifier, plan, book, shared),
            audit=rt.audit,
            approvals=rt.approvals,
            stop=StopConditions(
                max_iterations=revisions + 1,
                max_tokens=plan.budget.max_tokens,
                max_verifier_rejections=revisions + 1,
                timeout=timedelta(seconds=plan.budget.timeout_s),
            ),
        )
        result = await loop.run(
            goal=plan.goal,
            tenant_id=tenant,
            agent_id=AGENT_COMPOSER,
            as_of=self._as_of,
            run_id=f"compose-{uuid.uuid4().hex[:12]}",
        )
        verdicts = [
            VerdictTrace(
                iteration=n,
                decision=record.decision,
                reasons=list(record.reasons),
                flags=list(record.flags),
            )
            for n, record in enumerate(result.state.verdicts, start=1)
        ]
        return ComposeOutcome(
            final_text=result.final_answer if result.ok else None,
            verdicts=verdicts,
            drafts=shared.drafts,
            stop_reason=result.stop_reason.value if result.stop_reason else "unknown",
            tokens=result.state.tokens_used,
        )
