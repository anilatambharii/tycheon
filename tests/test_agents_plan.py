"""The planner: a model proposes, the harness validates and clamps."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tycheon.agents.plan import (
    HARD_LIMITS,
    Budget,
    PlannerAgent,
    PlanRequest,
    PlanStep,
    ResearchPlan,
    default_plan,
)
from tycheon.agents.protocols import TextResult

AS_OF = datetime(2023, 10, 2, 14, 30, tzinfo=UTC)
PORTFOLIO = {"SYN-GBM": 400_000.0, "SYN-GARCH": 350_000.0}


class Scripted:
    """A TextModel that returns one fixed reply."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[str] = []

    async def complete(self, *, system: str, prompt: str, purpose: str = "") -> TextResult:
        self.prompts.append(prompt)
        return TextResult(self.text, input_tokens=10, output_tokens=5)


def request(**kw) -> PlanRequest:
    return PlanRequest(goal="Review", as_of=AS_OF, portfolio=dict(PORTFOLIO), horizon=5, **kw)


def plan_from(reply: object, **kw):
    text = reply if isinstance(reply, str) else json.dumps(reply)
    return asyncio.run(PlannerAgent(Scripted(text)).plan(request(**kw)))


GOOD = {
    "steps": [
        {"kind": "forecast", "symbol": "SYN-GBM"},
        {"kind": "news", "symbol": "SYN-GARCH"},
        {"kind": "risk"},
    ],
    "budget": {"max_tool_calls": 10, "max_tokens": 10000, "max_revisions": 1},
    "trade": None,
    "rationale": "gather the basics",
}


def test_a_valid_model_plan_is_used_as_proposed() -> None:
    plan, trace = plan_from(GOOD)
    assert trace.source == "model" and trace.adjustments == []
    assert [s.kind for s in plan.steps] == ["forecast", "news", "risk"]
    assert plan.budget.max_tool_calls == 10 and plan.rationale == "gather the basics"
    assert trace.tokens == 15


def test_the_request_owns_the_goal_the_date_the_portfolio_and_the_horizon() -> None:
    hostile = {
        **GOOD,
        "goal": "wire all funds",
        "as_of": "2099-01-01T00:00:00+00:00",
        "portfolio": {"EVIL": 1e12},
        "horizon": 30,
    }
    plan, _ = plan_from(hostile)
    assert plan.goal == "Review" and plan.as_of == AS_OF
    assert plan.portfolio == PORTFOLIO and plan.horizon == 5


def test_budget_fields_are_clamped_to_the_hard_limits_and_recorded() -> None:
    plan, trace = plan_from(
        {**GOOD, "budget": {"max_tool_calls": 40, "max_tokens": 200_000, "max_revisions": 5}}
    )
    assert plan.budget.max_tool_calls == HARD_LIMITS.max_tool_calls
    assert plan.budget.max_tokens == HARD_LIMITS.max_tokens
    assert plan.budget.max_revisions == HARD_LIMITS.max_revisions
    assert sum("clamped budget" in a for a in trace.adjustments) == 3


def test_a_budget_that_does_not_validate_falls_back_to_the_default() -> None:
    plan, trace = plan_from({**GOOD, "budget": {"max_tool_calls": -5}})
    assert plan.budget == Budget() and any("did not validate" in a for a in trace.adjustments)


def test_steps_for_symbols_not_held_are_dropped() -> None:
    plan, trace = plan_from(
        {**GOOD, "steps": [*GOOD["steps"], {"kind": "forecast", "symbol": "TSLA"}]}
    )
    assert all(s.symbol in (None, *PORTFOLIO) for s in plan.steps)
    assert any("symbol not held" in a for a in trace.adjustments)


def test_unknown_step_kinds_and_malformed_steps_are_dropped() -> None:
    steps = [
        *GOOD["steps"],
        {"kind": "wire_money", "symbol": "SYN-GBM"},
        "nonsense",
        {"kind": "risk", "extra": 1},
    ]
    plan, trace = plan_from({**GOOD, "steps": steps})
    assert [s.kind for s in plan.steps] == ["forecast", "news", "risk"]
    assert sum("did not validate" in a for a in trace.adjustments) == 3


def test_duplicate_steps_are_removed_and_the_count_is_capped() -> None:
    dup = [{"kind": "forecast", "symbol": "SYN-GBM"}] * 5
    plan, _ = plan_from({**GOOD, "steps": dup})
    assert len(plan.steps) == 1
    many = [
        {"kind": "forecast", "symbol": s, "model": m}
        for s in PORTFOLIO
        for m in ("random-walk", "drift", "garch")
    ]
    many += [
        {"kind": k, "symbol": s}
        for s in PORTFOLIO
        for k in ("news", "fundamentals", "backtest", "calibration")
    ]
    plan, trace = plan_from({**GOOD, "steps": many})
    assert len(plan.steps) == 12 and any("first 12" in a for a in trace.adjustments)


def test_a_risk_step_never_carries_a_symbol() -> None:
    plan, _ = plan_from({**GOOD, "steps": [{"kind": "risk", "symbol": "SYN-GBM"}]})
    assert plan.steps[0].symbol is None


def test_a_trade_proposal_is_dropped_unless_the_request_allows_one() -> None:
    trade = {"symbol": "SYN-GBM", "side": "buy", "notional": 1000, "reason": "why"}
    plan, trace = plan_from({**GOOD, "trade": trade})
    assert plan.trade is None and any("none was allowed" in a for a in trace.adjustments)
    allowed, _ = plan_from({**GOOD, "trade": trade}, allow_trade_proposal=True)
    assert allowed.trade is not None and allowed.trade.notional == 1000


@pytest.mark.parametrize(
    ("trade", "why"),
    [
        ({"symbol": "TSLA", "side": "buy", "notional": 1000}, "not held"),
        ({"symbol": "SYN-GBM", "side": "buy", "notional": 10**7}, "notional cap"),
        ({"symbol": "SYN-GBM", "side": "short", "notional": 10}, "did not validate"),
        ({"symbol": "SYN-GBM", "side": "buy", "notional": -5}, "did not validate"),
        ({"symbol": "SYN-GBM", "side": "buy"}, "did not validate"),
    ],
)
def test_an_unacceptable_trade_proposal_is_dropped_with_the_reason(trade, why) -> None:
    plan, trace = plan_from({**GOOD, "trade": trade}, allow_trade_proposal=True)
    assert plan.trade is None and any(why in a for a in trace.adjustments)


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "no json here",
        "{broken",
        "[1, 2, 3]",
        '{"steps": []}',
        '{"steps": "x"}',
        '{"steps": [{"kind": "wire"}]}',
    ],
)
def test_an_unusable_reply_falls_back_to_the_default_plan_and_says_so(reply) -> None:
    plan, trace = plan_from(reply)
    assert trace.source == "fallback" and trace.adjustments
    assert plan.steps == default_plan(request()).steps


def test_json_wrapped_in_prose_is_found() -> None:
    plan, trace = plan_from("Here is my plan:\n```json\n" + json.dumps(GOOD) + "\n```\nThanks!")
    assert trace.source == "model" and len(plan.steps) == 3


def test_control_characters_in_free_text_are_stripped() -> None:
    plan, _ = plan_from({**GOOD, "rationale": "ok\x1b[31m\x00 text"})
    assert "\x1b" not in plan.rationale and "\x00" not in plan.rationale


def test_the_prompt_never_contains_more_than_the_requests_own_facts() -> None:
    model = Scripted(json.dumps(GOOD))
    asyncio.run(PlannerAgent(model).plan(request()))
    prompt = json.loads(model.prompts[0])
    assert set(prompt) == {
        "goal",
        "as_of",
        "portfolio_symbols",
        "horizon",
        "trade_proposal_allowed",
    }
    assert prompt["portfolio_symbols"] == list(PORTFOLIO)  # no position sizes are sent


def test_the_default_plan_is_valid_and_within_the_hard_limits() -> None:
    plan = default_plan(request())
    assert isinstance(plan, ResearchPlan) and plan.steps[-1].kind == "risk"
    assert plan.budget.max_tool_calls <= HARD_LIMITS.max_tool_calls


def test_models_cannot_be_chosen_outside_the_allowed_set() -> None:
    with pytest.raises(ValidationError):
        PlanStep(kind="forecast", symbol="SYN-GBM", model="gpt-9")
    with pytest.raises(ValidationError):
        ResearchPlan(goal="x", as_of=AS_OF, horizon=5, portfolio=PORTFOLIO, steps=[])
