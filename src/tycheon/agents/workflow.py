"""The agentic risk review: plan, gather evidence, draft, verify, revise, report, propose.

    Planner  ->  specialists (each a governed tool call)  ->  composer drafts
         ->  independent verifier  ->  (reject: revise, within budget)  ->  report
         ->  [verified only] save the report; propose a paper trade for human approval

What this module guarantees by construction:

* the model plans and drafts; it never calls a tool. Every tool call is made by a specialist
  through the :class:`~tycheon.agents.protocols.ToolCaller`, under that specialist's own grant;
* a draft that the verifier does not accept is never published or acted on (the report is
  *withheld*), and no trade is proposed from an unverified analysis;
* a paper trade can only ever be *proposed*: it resolves to a human approval, not an execution;
* every step, refusal and verifier verdict is recorded in the run's trace.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003 - used in a dataclass field at runtime
from typing import TYPE_CHECKING, Protocol

from tycheon.agents.composer import DraftWriter, Report, ReportComposer, VerdictTrace
from tycheon.agents.models import EvidenceBook, EvidenceGap
from tycheon.agents.plan import PlannerAgent, PlanRequest, PlanTrace, ResearchPlan
from tycheon.agents.protocols import NullTracer, TraceEvent
from tycheon.agents.specialists import (
    AGENT_COMPOSER,
    BacktestAgent,
    ForecastAgent,
    FundamentalsAgent,
    NewsAgent,
    RiskAgent,
    TradeProposer,
)
from tycheon.agents.verifier import ReportVerifier

if TYPE_CHECKING:
    from tycheon.agents.models import Evidence
    from tycheon.agents.plan import PlanStep
    from tycheon.agents.protocols import TextModel, ToolCaller, ToolResult, Tracer


class _Specialist(Protocol):
    agent_id: str

    async def run(self, step: PlanStep, plan: ResearchPlan) -> Evidence | None: ...


@dataclass(frozen=True)
class ReviewRequest:
    goal: str
    as_of: datetime
    portfolio: dict[str, float]
    horizon: int = 5
    allow_trade_proposal: bool = False
    max_trade_notional: float = 50_000.0


@dataclass
class ComposeOutcome:
    """What the draft-verify-revise cycle produced."""

    final_text: str | None
    verdicts: list[VerdictTrace]
    drafts: list[str]
    stop_reason: str
    tokens: int = 0


class ComposeRunner(Protocol):
    """Runs the draft -> verify -> revise cycle. Governance implements it on Keelgate's loop."""

    async def compose(
        self,
        *,
        plan: ResearchPlan,
        book: EvidenceBook,
        writer: DraftWriter,
        verifier: ReportVerifier,
    ) -> ComposeOutcome: ...


@dataclass
class ReviewResult:
    report: Report
    plan: ResearchPlan
    plan_trace: PlanTrace
    book: EvidenceBook
    compose: ComposeOutcome
    saved: ToolResult | None = None
    trade: ToolResult | None = None
    events: list[TraceEvent] = field(default_factory=list)


class RiskReviewWorkflow:
    def __init__(
        self,
        *,
        tools: ToolCaller,
        planner_model: TextModel,
        writer_model: TextModel,
        composer: ComposeRunner,
        verifier: ReportVerifier | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._tools = tools
        self._planner = PlannerAgent(planner_model)
        self._writer = DraftWriter(writer_model)
        self._composer = composer
        self._verifier = verifier or ReportVerifier()
        self._tracer = tracer or NullTracer()

    async def run(self, request: ReviewRequest) -> ReviewResult:
        events: list[TraceEvent] = []
        with self._tracer.span("tycheon.review", goal=request.goal[:120]) as root:
            plan, plan_trace = await self._plan(request, events)
            book = EvidenceBook()
            await self._gather(plan, book, events)
            compose = await self._compose(plan, book, events)
            saved: ToolResult | None = None
            trade: ToolResult | None = None
            if compose.final_text is not None:
                saved = await self._save(plan, compose.final_text, events)
                if plan.trade is not None:
                    trade = await self._propose(plan, book, compose, events)
            else:
                events.append(
                    TraceEvent(
                        "workflow", "withheld", "no draft passed verification; nothing saved"
                    )
                )
            report = ReportComposer.finalize(
                plan=plan,
                book=book,
                final_text=compose.final_text,
                verdicts=compose.verdicts,
                stop_reason=compose.stop_reason,
                tokens=compose.tokens + plan_trace.tokens,
                approvals=[trade] if trade is not None else [],
                saved_to=(saved.output or {}).get("path") if saved and saved.ok else None,
            )
            root.set_attribute("tycheon.report.status", report.status)
        return ReviewResult(report, plan, plan_trace, book, compose, saved, trade, events)

    # ------------------------------------------------------------------------ steps
    async def _plan(
        self, request: ReviewRequest, events: list[TraceEvent]
    ) -> tuple[ResearchPlan, PlanTrace]:
        with self._tracer.span("tycheon.plan"):
            plan, trace = await self._planner.plan(
                PlanRequest(
                    goal=request.goal,
                    as_of=request.as_of,
                    portfolio=dict(request.portfolio),
                    horizon=request.horizon,
                    allow_trade_proposal=request.allow_trade_proposal,
                    max_trade_notional=request.max_trade_notional,
                )
            )
        detail = (
            f"{len(plan.steps)} steps from the {trace.source}; budget: "
            f"{plan.budget.max_tool_calls} tool calls, {plan.budget.max_tokens} tokens, "
            f"{plan.budget.max_revisions} revisions"
        )
        events.append(TraceEvent("planner", "plan", detail, {"source": trace.source}))
        events += [TraceEvent("planner", "adjusted", a) for a in trace.adjustments]
        return plan, trace

    async def _gather(
        self, plan: ResearchPlan, book: EvidenceBook, events: list[TraceEvent]
    ) -> None:
        agents: dict[str, _Specialist] = {
            "forecast": ForecastAgent(self._tools, book, self._tracer),
            "calibration": ForecastAgent(self._tools, book, self._tracer),
            "risk": RiskAgent(self._tools, book, self._tracer),
            "news": NewsAgent(self._tools, book, self._tracer),
            "fundamentals": FundamentalsAgent(self._tools, book, self._tracer),
            "backtest": BacktestAgent(self._tools, book, self._tracer),
        }
        # one call per step, plus the report save and the optional trade proposal
        spendable = plan.budget.max_tool_calls - 1 - (1 if plan.trade else 0)
        calls = 0
        for step in plan.steps:
            label = f"{step.kind}[{step.symbol or 'portfolio'}]"
            if calls >= spendable:
                book.gaps.append(
                    EvidenceGap(
                        step=f"{step.kind}:{step.symbol or 'portfolio'}",
                        agent="workflow",
                        tool="-",
                        reason="the plan's tool-call budget was exhausted",
                    )
                )
                events.append(TraceEvent("workflow", "skipped", f"{label}: budget exhausted"))
                continue
            calls += 1
            before_gaps = len(book.gaps)
            agent = agents[step.kind]
            evidence = await agent.run(step, plan)
            events.append(self._step_event(agent.agent_id, label, evidence, book, before_gaps))

    @staticmethod
    def _step_event(
        agent_id: str,
        label: str,
        evidence: Evidence | None,
        book: EvidenceBook,
        before_gaps: int,
    ) -> TraceEvent:
        if evidence is None:
            reason = book.gaps[-1].reason if len(book.gaps) > before_gaps else "no evidence"
            return TraceEvent(agent_id, label, f"no evidence: {reason}", {"ok": False})
        status = f" ({evidence.calibration_status})" if evidence.calibration_status else ""
        flags = f"; flags: {', '.join(evidence.flags)}" if evidence.flags else ""
        return TraceEvent(
            agent_id, label, f"{evidence.id}{status}{flags}", {"ok": True, "evidence": evidence.id}
        )

    async def _compose(
        self, plan: ResearchPlan, book: EvidenceBook, events: list[TraceEvent]
    ) -> ComposeOutcome:
        with self._tracer.span("tycheon.compose", evidence=len(book)) as span:
            outcome = await self._composer.compose(
                plan=plan, book=book, writer=self._writer, verifier=self._verifier
            )
            span.set_attribute("tycheon.verifier.rejections", len(outcome.verdicts) - 1)
        for verdict in outcome.verdicts:
            reasons = "; ".join(verdict.reasons)
            events.append(
                TraceEvent(
                    "verifier",
                    f"draft {verdict.iteration}: {verdict.decision}",
                    reasons,
                    {"flags": verdict.flags},
                )
            )
        return outcome

    async def _save(
        self, plan: ResearchPlan, final_text: str, events: list[TraceEvent]
    ) -> ToolResult:
        report_id = (
            "review-"
            + hashlib.sha256(
                (plan.goal + plan.as_of.isoformat() + final_text).encode()
            ).hexdigest()[:12]
        )
        result = await self._tools.call(
            AGENT_COMPOSER,
            "save_report",
            {"report_id": report_id, "markdown": final_text},
            rationale="save the verified report",
        )
        events.append(TraceEvent(AGENT_COMPOSER, "save_report", f"{result.status} ({result.tool})"))
        return result

    async def _propose(
        self,
        plan: ResearchPlan,
        book: EvidenceBook,
        compose: ComposeOutcome,
        events: list[TraceEvent],
    ) -> ToolResult:
        assert plan.trade is not None  # noqa: S101 - checked by the caller
        flags = sorted({f for e in book.items for f in e.flags})
        result = await TradeProposer(self._tools, self._tracer).propose(
            plan.trade,
            plan,
            book,
            rationale=(compose.final_text or "")[:1500],
            flags=flags,
        )
        detail: str = result.status
        if result.approval_id:
            detail += f" approval_id={result.approval_id} tier={result.approval_tier}"
        if result.policy_reasons:
            detail += " (" + "; ".join(result.policy_reasons) + ")"
        events.append(TraceEvent("trade-proposer", "propose_paper_trade", detail))
        return result
