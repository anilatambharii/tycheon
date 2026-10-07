"""The promotion gate: a fine-tuned model is served only if it beats the baselines on held-out data.

The candidate is scored on the tenant's own walk-forward test region (bars it never trained on,
with an embargo), on the same origins as two references:

* the **random walk**, the baseline every evaluation in Tycheon is read against; and
* the **base model** it was fine-tuned from, so fine-tuning cannot make a model worse unnoticed.

It is promoted only if, pooled over the tenant's symbols, its CRPS is at least ``min_improvement``
below the random walk's, a one-sided Diebold-Mariano test says that gap is not noise, and it is not
worse than the base model. Too few test origins is a *failure*, not a pass: thin evidence never
promotes a model. Every outcome, including the numbers behind a rejection, is stored with the model.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from tycheon.backtest.metrics import SIGNIFICANCE, evaluate, pool_scores
from tycheon.backtest.walk_forward import WalkForwardConfig, static, walk_forward
from tycheon.models.baselines import RandomWalkForecaster

if TYPE_CHECKING:
    import pandas as pd

    from tycheon.models.base import Forecaster
    from tycheon_ft.dataset import FineTuneConfig

MIN_ORIGINS = 30
DEFAULT_MIN_IMPROVEMENT = 0.02  # CRPS must be at least 2% below the random walk's


@dataclass
class GateResult:
    passed: bool
    reasons: list[str]
    n_origins: int
    candidate: dict[str, Any] = field(default_factory=dict)
    random_walk: dict[str, Any] = field(default_factory=dict)
    base_model: dict[str, Any] = field(default_factory=dict)
    dm_vs_random_walk: dict[str, Any] = field(default_factory=dict)
    dm_vs_base: dict[str, Any] = field(default_factory=dict)
    min_improvement: float = DEFAULT_MIN_IMPROVEMENT

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reasons": self.reasons,
            "n_origins": self.n_origins,
            "candidate": self.candidate,
            "baseline": self.random_walk,
            "base_model": self.base_model,
            "dm_vs_random_walk": self.dm_vs_random_walk,
            "dm_vs_base": self.dm_vs_base,
            "min_improvement": self.min_improvement,
        }


def _row(ev: Any) -> dict[str, Any]:
    return {
        "model_id": ev.model_id,
        "crps": None if math.isnan(ev.crps) else float(ev.crps),
        "mase": None if math.isnan(ev.mase) else float(ev.mase),
        "n_origins": ev.n_origins,
    }


def score_models(
    series: dict[str, pd.DataFrame],
    models: dict[str, Forecaster],
    config: FineTuneConfig,
) -> dict[str, Any]:
    """Pooled walk-forward scores for each named forecaster, on identical origins."""
    wf = WalkForwardConfig(
        horizon=config.horizon,
        folds=config.folds,
        test_window=config.test_window,
        embargo=config.horizon,
        n_samples=config.eval_samples,
        max_history=config.lookback,
        min_history=60,
        seed=config.seed,
    )
    pooled: dict[str, Any] = {}
    for name, forecaster in models.items():
        per_symbol = [
            replace(walk_forward(static(forecaster), bars, wf).scores, model_id=name)
            for bars in series.values()
        ]
        pooled[name] = pool_scores(per_symbol, model_id=name)
    return pooled


def decide(
    scores: dict[str, Any], *, min_improvement: float = DEFAULT_MIN_IMPROVEMENT
) -> GateResult:
    """Apply the gate to pooled scores keyed ``candidate``, ``random-walk`` and ``base``."""
    rw, cand, base = scores["random-walk"], scores["candidate"], scores["base"]
    if cand.n < MIN_ORIGINS:
        return GateResult(
            False,
            [f"only {cand.n} test origins; at least {MIN_ORIGINS} are needed to judge a model"],
            cand.n,
            min_improvement=min_improvement,
        )
    ev_rw = evaluate(cand, rw)
    ev_base = evaluate(cand, base)
    ev_base_vs_rw = evaluate(base, rw)
    reasons: list[str] = []
    passed = True
    crps_c, crps_rw, crps_b = ev_rw.crps, evaluate(rw, rw).crps, ev_base_vs_rw.crps
    required = crps_rw * (1.0 - min_improvement)
    if not crps_c <= required:
        passed = False
        reasons.append(
            f"CRPS {crps_c:.6g} is not at least {min_improvement:.0%} below the random walk's "
            f"{crps_rw:.6g} (it would need to be {required:.6g} or lower)"
        )
    p_rw = ev_rw.dm["crps"].p_model_better
    if math.isnan(p_rw) or not p_rw < SIGNIFICANCE:
        passed = False
        reasons.append(
            f"the Diebold-Mariano test cannot tell it apart from the random walk "
            f"(one-sided p = {p_rw:.3g}, needed below {SIGNIFICANCE})"
        )
    if crps_c > crps_b:
        passed = False
        reasons.append(
            f"it is worse than the base model it was fine-tuned from (CRPS {crps_c:.6g} vs "
            f"{crps_b:.6g}): fine-tuning did not help"
        )
    if passed:
        reasons.append(
            f"CRPS {crps_c:.6g} is {1 - crps_c / crps_rw:.1%} below the random walk's "
            f"{crps_rw:.6g} (DM p = {p_rw:.3g}) and no worse than the base model"
        )
    return GateResult(
        passed=passed,
        reasons=reasons,
        n_origins=cand.n,
        candidate=_row(ev_rw),
        random_walk=_row(evaluate(rw, rw)),
        base_model=_row(ev_base_vs_rw),
        dm_vs_random_walk=ev_rw.dm["crps"].to_dict(),
        dm_vs_base=ev_base.dm["crps"].to_dict(),
        min_improvement=min_improvement,
    )


def run_gate(
    series: dict[str, pd.DataFrame],
    candidate: Forecaster,
    base: Forecaster,
    config: FineTuneConfig,
    *,
    min_improvement: float = DEFAULT_MIN_IMPROVEMENT,
) -> GateResult:
    scores = score_models(
        series,
        {"random-walk": RandomWalkForecaster(), "candidate": candidate, "base": base},
        config,
    )
    return decide(scores, min_improvement=min_improvement)
