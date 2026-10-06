"""Tycheon's policy: Keelgate's ``finance_basic`` pack plus a small, fail-closed overlay.

Keelgate's pack is deny-by-default and only knows the capabilities it was written for
(market data, trade, report). Tycheon adds read-only analytics capabilities, and one rule the
pack does not have: **a paper-trade proposal is never auto-approved**, whatever its size.

The overlay is deliberately tiny and deterministic:

* a non-paper execution mode is denied before anything else;
* the read-only analytics capabilities are allowed, and only as ``READ`` tools;
* anything else is decided by ``finance_basic`` (limits, restricted symbols, trading hours,
  approval tiers), and where that says ``ALLOW`` for a paper trade it is *raised* to
  ``REQUIRE_APPROVAL``. The overlay can raise a decision and can deny; it never lowers one;
* any error at all is a denial. It fails closed.
"""

from __future__ import annotations

import functools
from typing import Any

from keelgate.approvals import ApprovalTier
from keelgate.policy import (
    Decision,
    PolicyContext,
    PolicyDecision,
    PolicyEngine,
    PolicyInput,
)

from tycheon.governance._rego_process import OutOfProcessRegoEngine

OVERLAY_VERSION = "tycheon-overlay-1"
PAPER_MODES = frozenset({"paper", "simulation"})

#: Read-only analytics: no side effects, so the policy may allow them outright.
ANALYTICS_CAPABILITIES = frozenset(
    {
        "forecast:run",
        "calibration:run",
        "risk:compute",
        "backtest:run",
        "news:read",
        "fundamentals:read",
    }
)
#: Capabilities whose approval can never be skipped.
ALWAYS_APPROVE = frozenset({"trade:paper_execute"})

#: Trusted limits supplied to the policy (never derived from model output). The one-click and
#: explicit thresholds are Keelgate's; the overlay applies at least ONE_CLICK to every trade.
DEFAULT_LIMITS: dict[str, Any] = {
    "max_notional_per_action": 50_000,
    "max_daily_exposure": 250_000,
    "restricted_symbols": [],
    "approval_one_click_notional": 10_000,
    "approval_explicit_notional": 25_000,
    # The review's as_of may be any weekday; weekends are denied by the pack for trades.
    "trading_hours": {"tz": "America/New_York", "open_minute": 570, "close_minute": 960},
}


@functools.lru_cache(maxsize=1)
def _default_engine() -> PolicyEngine:
    """One shared worker process for every default policy (see ``_rego_process``)."""
    return OutOfProcessRegoEngine()


class TycheonPolicy:
    """A :class:`~keelgate.policy.PolicyEngine`: ``finance_basic`` plus the overlay above."""

    name = "tycheon-policy"

    def __init__(self, inner: PolicyEngine | None = None) -> None:
        self._inner: PolicyEngine = inner if inner is not None else _default_engine()
        self._inner_version = str(getattr(self._inner, "policy_version", "unknown"))

    @property
    def policy_version(self) -> str:
        return f"{OVERLAY_VERSION}+{self._inner_version}"

    def _deny(self, reason: str) -> PolicyDecision:
        return PolicyDecision(
            effect=Decision.DENY,
            reasons=(reason,),
            policy_version=self.policy_version,
            engine=self.name,
        )

    async def decide(self, policy_input: PolicyInput) -> PolicyDecision:
        try:
            return await self._decide(policy_input)
        except Exception:  # fail closed on anything at all, including a broken inner engine
            return self._deny("policy evaluation failed")

    async def _decide(self, policy_input: PolicyInput) -> PolicyDecision:
        action, context = policy_input.action, policy_input.context
        if context.execution_mode not in PAPER_MODES:
            return self._deny("live execution is forbidden: mode must be paper or simulation")
        capability = action.capability
        if capability in ANALYTICS_CAPABILITIES:
            if action.side_effect != "READ":
                return self._deny("analytics capabilities are read-only")
            return PolicyDecision(
                effect=Decision.ALLOW,
                reasons=("read-only analytics",),
                policy_version=self.policy_version,
                engine=self.name,
            )
        inner = await self._inner.decide(policy_input)
        if capability in ALWAYS_APPROVE and inner.effect is Decision.ALLOW:
            return PolicyDecision(
                effect=Decision.REQUIRE_APPROVAL,
                reasons=("paper trades always need human approval",),
                approval_tier=ApprovalTier.ONE_CLICK,
                policy_version=self.policy_version,
                engine=self.name,
            )
        return inner.model_copy(update={"policy_version": self.policy_version, "engine": self.name})


def policy_context(
    as_of: Any, *, limits: dict[str, Any] | None = None, daily_notional: float = 0.0
) -> PolicyContext:
    """The trusted facts handed to the policy for a call. Harness code only."""
    return PolicyContext(
        as_of=as_of,
        execution_mode="paper",
        limits=dict(DEFAULT_LIMITS if limits is None else limits),
        exposure={"daily_notional": daily_notional},
    )
