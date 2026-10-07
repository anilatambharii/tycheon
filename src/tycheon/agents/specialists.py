"""The specialist agents: each gathers one kind of evidence through governed tools.

Each specialist has its **own agent id and its own capability grant** (least privilege): the
forecast agent can run forecasts and nothing else, the news agent can read news and nothing
else, and so on. They are deterministic code, not free-running models: a specialist maps a plan
step to one tool call, reads the governed result, and records it as evidence. A refusal or an
error becomes an :class:`~tycheon.agents.models.EvidenceGap`, never a guess.

The :class:`NewsAgent` handles untrusted documents. It never sees their text: the governed news
tool returns only bounded numbers and flags (see :mod:`tycheon.services.news`), so there is no
document text for an instruction inside it to ride on.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Any

from tycheon.agents.models import EvidenceGap, flatten_numbers
from tycheon.agents.protocols import EvidenceRef, NullTracer

if TYPE_CHECKING:
    from tycheon.agents.models import Evidence, EvidenceBook
    from tycheon.agents.plan import PlanStep, ResearchPlan, TradeProposalSpec
    from tycheon.agents.protocols import ToolCaller, ToolResult, Tracer

AGENT_FORECAST = "forecast-agent"
AGENT_RISK = "risk-agent"
AGENT_NEWS = "news-agent"
AGENT_FUNDAMENTALS = "fundamentals-agent"
AGENT_BACKTEST = "backtest-agent"
AGENT_COMPOSER = "report-composer"
AGENT_TRADER = "trade-proposer"


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class _Specialist:
    agent_id = ""

    def __init__(self, tools: ToolCaller, book: EvidenceBook, tracer: Tracer | None = None) -> None:
        self._tools = tools
        self._book = book
        self._tracer = tracer or NullTracer()

    async def _call(
        self, step: PlanStep, tool: str, args: dict[str, Any]
    ) -> tuple[ToolResult | None, dict[str, Any]]:
        """Run one governed call; on a refusal or error record a gap and return ``None``."""
        with self._tracer.span(f"agent.{self.agent_id}", tool=tool) as span:
            result = await self._tools.call(
                self.agent_id, tool, args, rationale=f"plan step: {step.kind}"
            )
            span.set_attribute("tycheon.tool.status", result.status)
        if not result.ok or result.output is None:
            reason = f"{result.status}: {result.error_code or ''} {result.message}".strip()
            if result.policy_reasons:
                reason += " (" + "; ".join(result.policy_reasons) + ")"
            self._book.gaps.append(
                EvidenceGap(
                    step=f"{step.kind}:{step.symbol or 'portfolio'}",
                    agent=self.agent_id,
                    tool=tool,
                    reason=reason[:300],
                )
            )
            return None, {}
        return result, result.output

    def _record(
        self,
        step: PlanStep,
        plan: ResearchPlan,
        tool: str,
        result: ToolResult,
        output: dict[str, Any],
        *,
        kind: str,
        published_at: datetime,
        status: str | None = None,
        flags: list[str] | None = None,
    ) -> Evidence:
        return self._book.add(
            kind=kind,
            tool=tool,
            agent=self.agent_id,
            symbol=step.symbol,
            as_of=plan.as_of,
            published_at=published_at,
            calibration_status=status,
            numbers=flatten_numbers(output),
            data=output,
            audit_seq=result.audit_seq,
            flags=flags or [],
        )


class ForecastAgent(_Specialist):
    agent_id = AGENT_FORECAST

    async def run(self, step: PlanStep, plan: ResearchPlan) -> Evidence | None:
        assert step.symbol is not None  # noqa: S101 - validated by the planner
        if step.kind == "calibration":
            tool, kind = "calibration_report", "calibration"
            args: dict[str, Any] = {
                "symbol": step.symbol,
                "horizon": plan.horizon,
                "model": step.model,
            }
        else:
            tool, kind = "forecast_distribution", "forecast"
            args = {"symbol": step.symbol, "horizon": plan.horizon, "model": step.model}
        result, out = await self._call(step, tool, args)
        if result is None:
            return None
        status = out.get("calibration_status", out.get("status"))
        flags = ["uncalibrated_output"] if status in ("uncalibrated", "stale") else []
        return self._record(
            step, plan, tool, result, out, kind=kind, published_at=_parse(out["as_of"]),
            status=status, flags=flags,
        )  # fmt: skip


class RiskAgent(_Specialist):
    agent_id = AGENT_RISK

    async def run(self, step: PlanStep, plan: ResearchPlan) -> Evidence | None:
        args = {
            "positions": plan.portfolio,
            "horizon": plan.horizon,
            "model": step.model,
            "coupling": "terminal",
        }
        result, out = await self._call(step, "portfolio_risk", args)
        if result is None:
            return None
        status = out["portfolio_calibration_status"]
        flags = ["uncalibrated_output"] if status in ("uncalibrated", "stale") else []
        if any("not reliable" in w for w in out.get("warnings", [])):
            flags.append("unreliable_tail")
        return self._record(
            step, plan, "portfolio_risk", result, out, kind="risk",
            published_at=_parse(out["as_of"]), status=status, flags=flags,
        )  # fmt: skip


class BacktestAgent(_Specialist):
    agent_id = AGENT_BACKTEST

    async def run(self, step: PlanStep, plan: ResearchPlan) -> Evidence | None:
        assert step.symbol is not None  # noqa: S101
        args = {"symbol": step.symbol, "horizon": min(plan.horizon, 20)}
        result, out = await self._call(step, "backtest_summary", args)
        if result is None:
            return None
        return self._record(
            step, plan, "backtest_summary", result, out, kind="backtest",
            published_at=_parse(out["as_of"]),
        )  # fmt: skip


class NewsAgent(_Specialist):
    agent_id = AGENT_NEWS

    async def run(self, step: PlanStep, plan: ResearchPlan) -> Evidence | None:
        assert step.symbol is not None  # noqa: S101
        result, out = await self._call(step, "news_signals", {"symbol": step.symbol})
        if result is None:
            return None
        flags = []
        if out.get("n_injection_suspected", 0) > 0:
            flags.append("injection_suspected")
        if out.get("n_excluded_after_as_of", 0) > 0:
            flags.append("future_documents_excluded")
        latest = max((_parse(s["published_at"]) for s in out.get("signals", [])), default=None)
        return self._record(
            step, plan, "news_signals", result, out, kind="news",
            published_at=latest or plan.as_of, flags=flags,
        )  # fmt: skip


class FundamentalsAgent(_Specialist):
    agent_id = AGENT_FUNDAMENTALS

    async def run(self, step: PlanStep, plan: ResearchPlan) -> Evidence | None:
        assert step.symbol is not None  # noqa: S101
        result, out = await self._call(step, "fundamentals_snapshot", {"symbol": step.symbol})
        if result is None:
            return None
        metrics = out.get("metrics", {})
        if not metrics:
            self._book.gaps.append(
                EvidenceGap(
                    step=f"fundamentals:{step.symbol}",
                    agent=self.agent_id,
                    tool="fundamentals_snapshot",
                    reason="no fundamentals were published as of the review date",
                )
            )
            return None
        latest = max(_parse(m["available_at"]) for m in metrics.values())
        return self._record(
            step, plan, "fundamentals_snapshot", result, out, kind="fundamentals",
            published_at=latest,
        )  # fmt: skip


class TradeProposer:
    """Turns an accepted analysis into a *paper* trade proposal. It can only propose.

    The governance policy resolves every proposal to ``REQUIRE_APPROVAL``; this agent has no way
    to execute one, and receives an approval id, not a fill.
    """

    agent_id = AGENT_TRADER

    def __init__(self, tools: ToolCaller, tracer: Tracer | None = None) -> None:
        self._tools = tools
        self._tracer = tracer or NullTracer()

    async def propose(
        self,
        spec: TradeProposalSpec,
        plan: ResearchPlan,
        book: EvidenceBook,
        *,
        rationale: str,
        flags: list[str],
    ) -> ToolResult:
        identity = f"{plan.goal}|{plan.as_of.isoformat()}|{spec.symbol}|{spec.side}|{spec.notional}"
        order_id = "prop-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        refs = [
            EvidenceRef(uri=f"evidence:{e.id}", title=f"{e.kind} {e.symbol or 'portfolio'}"[:300])
            for e in book.items
        ]
        with self._tracer.span(f"agent.{self.agent_id}", tool="propose_paper_trade") as span:
            result = await self._tools.call(
                self.agent_id,
                "propose_paper_trade",
                {
                    "symbol": spec.symbol,
                    "side": spec.side,
                    "notional": spec.notional,
                    "client_order_id": order_id,
                },
                rationale=(spec.reason + " | " + rationale)[:3900],
                evidence=refs,
                flags=flags,
            )
            span.set_attribute("tycheon.tool.status", result.status)
        return result
