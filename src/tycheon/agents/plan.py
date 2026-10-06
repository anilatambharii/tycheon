"""The Planner: a typed, budgeted research plan whose limits the harness, not the model, owns.

A language model proposes the plan as JSON. Everything it says is then *validated and clamped*
by deterministic code:

* the goal, the ``as_of``, the portfolio and the horizon are the **request's**, not the model's;
  a plan cannot change what is being reviewed or the date it is reviewed as of;
* steps are limited to known kinds and to symbols in the portfolio;
* every budget field is clamped to hard limits the model cannot raise;
* a trade proposal is dropped unless the request allows one, and only for a held symbol within
  a notional cap.

If the model's reply cannot be turned into a valid plan, a deterministic default plan is used and
the trace says so. Every adjustment is recorded in :class:`PlanTrace` so nothing is silent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003 - pydantic needs it at runtime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tycheon.services.schemas import (  # noqa: TC001 - pydantic needs them at runtime
    ModelName,
    Symbol,
)

if TYPE_CHECKING:
    from tycheon.agents.protocols import TextModel

StepKind = Literal["forecast", "calibration", "risk", "news", "fundamentals", "backtest"]
SYMBOL_KINDS: frozenset[str] = frozenset(
    {"forecast", "calibration", "news", "fundamentals", "backtest"}
)
MAX_STEPS = 12
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Budget(_Strict):
    max_tool_calls: int = Field(default=16, ge=1, le=40)
    max_tokens: int = Field(default=24_000, ge=1_000, le=200_000)
    max_dollars: float | None = Field(default=None, gt=0, le=100)
    max_revisions: int = Field(default=2, ge=0, le=5)
    timeout_s: int = Field(default=600, ge=10, le=3600)


#: The most any plan may ask for. A model cannot raise these.
HARD_LIMITS = Budget(
    max_tool_calls=24, max_tokens=60_000, max_dollars=5.0, max_revisions=3, timeout_s=900
)


class PlanStep(_Strict):
    kind: StepKind
    symbol: Symbol | None = None
    model: ModelName = "random-walk"
    note: str = Field(default="", max_length=200)


class TradeProposalSpec(_Strict):
    symbol: Symbol
    side: Literal["buy", "sell"]
    notional: float = Field(gt=0, le=1e9, allow_inf_nan=False)
    reason: str = Field(default="", max_length=300)


class ResearchPlan(_Strict):
    goal: str = Field(max_length=500)
    as_of: datetime
    horizon: int = Field(ge=1, le=30)
    portfolio: dict[Symbol, float]
    steps: list[PlanStep] = Field(min_length=1, max_length=MAX_STEPS)
    budget: Budget = Field(default_factory=Budget)
    trade: TradeProposalSpec | None = None
    rationale: str = Field(default="", max_length=600)

    def symbols(self) -> list[str]:
        return list(self.portfolio)


@dataclass(frozen=True)
class PlanRequest:
    """What the harness asks for. The model never overrides any of it."""

    goal: str
    as_of: datetime
    portfolio: dict[str, float]
    horizon: int = 5
    allow_trade_proposal: bool = False
    max_trade_notional: float = 50_000.0


@dataclass
class PlanTrace:
    """How the plan came to be: from the model or the fallback, and what was adjusted."""

    source: Literal["model", "fallback"]
    raw_text: str = ""
    adjustments: list[str] = field(default_factory=list)
    tokens: int = 0


def default_plan(request: PlanRequest) -> ResearchPlan:
    """The deterministic plan used when the model's plan is unusable."""
    symbols = list(request.portfolio)[:3]
    steps: list[PlanStep] = []
    for symbol in symbols:
        steps += [
            PlanStep(kind="forecast", symbol=symbol),
            PlanStep(kind="news", symbol=symbol),
            PlanStep(kind="fundamentals", symbol=symbol),
        ]
    steps.append(PlanStep(kind="risk"))
    return ResearchPlan(
        goal=request.goal[:500],
        as_of=request.as_of,
        horizon=request.horizon,
        portfolio=dict(request.portfolio),
        steps=steps,
        budget=Budget(),
        rationale="default plan: forecast, news and fundamentals per holding, then portfolio risk",
    )


SYSTEM_PROMPT = (
    "You plan a portfolio risk review. Reply with one JSON object only. "
    "You choose which evidence to gather; you do not execute anything and you cannot change "
    "the portfolio, the date or the limits. "
    'Step kinds: "forecast" and "calibration" and "news" and "fundamentals" and "backtest" '
    '(each with a "symbol" from the portfolio) and "risk" (whole portfolio, no symbol). '
    'JSON shape: {"steps": [{"kind": ..., "symbol": ..., "model": "random-walk"}], '
    '"budget": {"max_tool_calls": n, "max_tokens": n, "max_revisions": n}, '
    '"trade": null or {"symbol": ..., "side": "buy"|"sell", "notional": n, "reason": ...}, '
    '"rationale": "one sentence"}. '
    "Any trade is only a proposal that a human must approve."
)


def _clean(text: str, limit: int) -> str:
    return _CONTROL.sub("", text)[:limit]


def _extract_json(text: str) -> object:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    return json.loads(text[start : end + 1])


class PlannerAgent:
    """Asks a model for a plan, then validates and clamps it. See the module docstring."""

    def __init__(self, model: TextModel) -> None:
        self._model = model

    async def plan(self, request: PlanRequest) -> tuple[ResearchPlan, PlanTrace]:
        prompt = json.dumps(
            {
                "goal": request.goal,
                "as_of": request.as_of.isoformat(),
                "portfolio_symbols": list(request.portfolio),
                "horizon": request.horizon,
                "trade_proposal_allowed": request.allow_trade_proposal,
            }
        )
        result = await self._model.complete(system=SYSTEM_PROMPT, prompt=prompt, purpose="plan")
        trace = PlanTrace(
            source="model",
            raw_text=_clean(result.text, 4000),
            tokens=result.input_tokens + result.output_tokens,
        )
        try:
            return self._validate(request, _extract_json(result.text), trace), trace
        except (ValueError, ValidationError, TypeError) as exc:
            trace.source = "fallback"
            trace.adjustments.append(
                f"the model's plan was unusable ({type(exc).__name__}); used the default plan"
            )
            return default_plan(request), trace

    # ------------------------------------------------------------------ validation
    def _validate(self, request: PlanRequest, data: object, trace: PlanTrace) -> ResearchPlan:
        if not isinstance(data, dict):
            raise TypeError("the plan must be a JSON object")
        held = set(request.portfolio)
        steps = self._steps(data.get("steps"), held, trace)
        if not steps:
            raise ValueError("no usable steps")
        return ResearchPlan(
            goal=request.goal[:500],
            as_of=request.as_of,
            horizon=request.horizon,
            portfolio=dict(request.portfolio),
            steps=steps,
            budget=self._budget(data.get("budget"), trace),
            trade=self._trade(data.get("trade"), request, trace),
            rationale=_clean(str(data.get("rationale", "")), 600),
        )

    @staticmethod
    def _steps(raw: object, held: set[str], trace: PlanTrace) -> list[PlanStep]:
        steps: list[PlanStep] = []
        seen: set[tuple[str, str | None, str]] = set()
        for item in raw if isinstance(raw, list) else []:
            try:
                step = PlanStep.model_validate(item)
            except ValidationError:
                trace.adjustments.append("dropped a step that did not validate")
                continue
            if step.kind in SYMBOL_KINDS and step.symbol not in held:
                trace.adjustments.append(f"dropped a {step.kind} step for a symbol not held")
                continue
            if step.kind == "risk":
                step = step.model_copy(update={"symbol": None})
            key = (step.kind, step.symbol, step.model)
            if key in seen:
                continue
            seen.add(key)
            steps.append(step)
            if len(steps) == MAX_STEPS:
                trace.adjustments.append(f"kept only the first {MAX_STEPS} steps")
                break
        return steps

    @staticmethod
    def _budget(raw: object, trace: PlanTrace) -> Budget:
        try:
            asked = Budget.model_validate(raw or {})
        except ValidationError:
            trace.adjustments.append("the budget did not validate; used the default budget")
            return Budget()
        clamped = {}
        for name in Budget.model_fields:
            value, ceiling = getattr(asked, name), getattr(HARD_LIMITS, name)
            if value is not None and ceiling is not None and value > ceiling:
                trace.adjustments.append(f"clamped budget.{name} from {value} to {ceiling}")
                value = ceiling
            clamped[name] = value
        return Budget(**clamped)

    @staticmethod
    def _trade(raw: object, request: PlanRequest, trace: PlanTrace) -> TradeProposalSpec | None:
        if not raw:
            return None
        if not request.allow_trade_proposal:
            trace.adjustments.append("dropped a trade proposal: none was allowed for this request")
            return None
        try:
            spec = TradeProposalSpec.model_validate(raw)
        except ValidationError:
            trace.adjustments.append("dropped a trade proposal that did not validate")
            return None
        if spec.symbol not in request.portfolio:
            trace.adjustments.append("dropped a trade proposal for a symbol not held")
            return None
        if spec.notional > request.max_trade_notional:
            trace.adjustments.append(
                f"dropped a trade proposal above the {request.max_trade_notional:g} notional cap"
            )
            return None
        return spec.model_copy(update={"reason": _clean(spec.reason, 300)})
