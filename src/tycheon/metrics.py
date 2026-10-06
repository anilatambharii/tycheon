"""Tycheon's financial metrics, packaged for Keelgate's outcome-metric plugin point.

AGENTS.md: Tycheon registers CRPS, coverage, MASE versus the random walk, the Diebold-Mariano
test and QLIKE through the entry-point group ``keelgate.outcome_metrics`` (declared in
``pyproject.toml``). The group name is part of Keelgate's integration contract.

.. important::
   Keelgate's ``OutcomeMetric`` protocol is **not implemented yet** (``keelgate.evals`` is an
   empty module and the contract lists it as planned), so its method signatures are not
   specified. The classes here use a deliberately small shape of Tycheon's own:

   * ``name`` and ``higher_is_better`` attributes;
   * ``compute(model, benchmark=None) -> MetricResult`` over walk-forward
     :class:`~tycheon.calibration.scores.ScoreSet` records.

   When Keelgate publishes the protocol, adapt it here (a thin wrapper in
   ``tycheon.governance``); the metric maths does not change. Until then, discovery by
   Keelgate cannot be tested, and the registration is a declaration of intent.

This module imports no Keelgate code, by design.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from tycheon.backtest.metrics import (
    crps_per_origin,
    evaluate,
    forecast_variance,
    interval_stats,
    mase,
    qlike,
    realized_variance,
)

if TYPE_CHECKING:
    from tycheon.calibration.scores import ScoreSet


@dataclass(frozen=True)
class MetricResult:
    """One metric on one model. ``value`` is ``None`` when it cannot be computed."""

    name: str
    value: float | None
    higher_is_better: bool
    details: dict[str, float | int | str] = field(default_factory=dict)


@runtime_checkable
class OutcomeMetric(Protocol):
    """Tycheon's local stand-in for Keelgate's (not yet defined) protocol."""

    name: str
    higher_is_better: bool

    def compute(self, model: ScoreSet, benchmark: ScoreSet | None = None) -> MetricResult: ...


def _finite(x: float) -> float | None:
    return float(x) if math.isfinite(x) else None


class CRPS:
    """Mean continuous ranked probability score of the horizon-end return (lower is better)."""

    name = "crps"
    higher_is_better = False

    def compute(self, model: ScoreSet, benchmark: ScoreSet | None = None) -> MetricResult:  # noqa: ARG002
        per_origin, approximate = crps_per_origin(model)
        return MetricResult(
            self.name,
            _finite(float(per_origin.mean())),
            self.higher_is_better,
            {"n_origins": model.n, "approximate": int(approximate)},
        )


class IntervalCoverage:
    """Achieved share of outcomes inside the central ``level`` interval (closer to ``level``)."""

    higher_is_better = False  # not monotone: the target is the nominal level, not 1

    def __init__(self, level: float = 0.9) -> None:
        if not 0.0 < level < 1.0:
            raise ValueError("level must be strictly between 0 and 1")
        self.level = level
        self.name = f"coverage_{round(level * 100)}"

    def compute(self, model: ScoreSet, benchmark: ScoreSet | None = None) -> MetricResult:  # noqa: ARG002
        got = interval_stats(model, self.level)
        if got is None:
            return MetricResult(self.name, None, self.higher_is_better, {"nominal": self.level})
        coverage, width = got
        return MetricResult(
            self.name,
            _finite(coverage),
            self.higher_is_better,
            {"nominal": self.level, "mean_width": width, "gap": abs(coverage - self.level)},
        )


class MASEvsRandomWalk:
    """MAE over the no-change forecast's MAE on the same origins (below 1 beats the walk)."""

    name = "mase_vs_random_walk"
    higher_is_better = False

    def compute(self, model: ScoreSet, benchmark: ScoreSet | None = None) -> MetricResult:  # noqa: ARG002
        return MetricResult(
            self.name, _finite(mase(model)), self.higher_is_better, {"n_origins": model.n}
        )


class DieboldMarianoVsRandomWalk:
    """One-sided Diebold-Mariano p-value that the model beats the random walk (lower is better).

    ``loss`` is ``"squared_error"`` or ``"crps"``. Needs the random walk's record as
    ``benchmark``, scored at the same origins.
    """

    higher_is_better = False

    def __init__(self, loss: str = "squared_error") -> None:
        if loss not in ("squared_error", "crps"):
            raise ValueError("loss must be 'squared_error' or 'crps'")
        self.loss = loss
        self.name = f"dm_p_{loss}"

    def compute(self, model: ScoreSet, benchmark: ScoreSet | None = None) -> MetricResult:
        if benchmark is None:
            raise ValueError("the Diebold-Mariano metric needs the random walk as benchmark")
        test = evaluate(model, benchmark).dm[self.loss]
        return MetricResult(
            self.name,
            _finite(test.p_model_better),
            self.higher_is_better,
            {
                "statistic": test.statistic,
                "mean_loss_difference": test.mean_diff,
                "n": test.n,
                "lags": test.lags,
            },
        )


class QLIKE:
    """QLIKE loss of the forecast variance against realised variance (lower is better)."""

    name = "qlike"
    higher_is_better = False

    def compute(self, model: ScoreSet, benchmark: ScoreSet | None = None) -> MetricResult:  # noqa: ARG002
        forecast = forecast_variance(model)
        if forecast is None:
            return MetricResult(
                self.name, None, self.higher_is_better, {"reason": "model has no sample paths"}
            )
        return MetricResult(
            self.name,
            _finite(qlike(forecast, realized_variance(model))),
            self.higher_is_better,
            {"n_origins": model.n},
        )


#: What the ``keelgate.outcome_metrics`` entry points expose, by registered name.
REGISTERED: dict[str, type[OutcomeMetric]] = {
    "crps": CRPS,
    "coverage": IntervalCoverage,
    "mase_vs_rw": MASEvsRandomWalk,
    "dm_test": DieboldMarianoVsRandomWalk,
    "qlike": QLIKE,
}
