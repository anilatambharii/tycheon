"""The promotion gate, on real walk-forward scores (no database, no torch).

Each test hands the gate a candidate and a base model and checks the decision and the reasons it
gives. The forecasters are the open-source baselines: a drift model really does beat the random
walk on a trending series and really does not on a driftless one, so the verdicts are earned.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from tycheon.data.schema import normalize_bars
from tycheon.models.baselines import DriftForecaster, RandomWalkForecaster
from tycheon_ft.dataset import FineTuneConfig
from tycheon_ft.gate import MIN_ORIGINS, run_gate

CONFIG = FineTuneConfig(
    symbols=["AAA"], horizon=5, lookback=120, test_window=120, folds=2, eval_samples=50
)


def series(n: int = 700, *, drift: float, vol: float, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    index = pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC", name="timestamp")
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": 1000.0,
        },
        index=index,
    )
    return normalize_bars(frame, bar_duration=pd.Timedelta(days=1))


TRENDING = {"AAA": series(drift=0.006, vol=0.006)}
DRIFTLESS = {"AAA": series(drift=0.0, vol=0.01, seed=11)}


def test_a_model_that_clearly_beats_the_random_walk_is_promoted() -> None:
    gate = run_gate(TRENDING, DriftForecaster(window=60), RandomWalkForecaster(), CONFIG)
    assert gate.passed, gate.reasons
    assert gate.n_origins >= MIN_ORIGINS
    assert gate.candidate["crps"] < gate.random_walk["crps"] * 0.98
    assert gate.dm_vs_random_walk["p_model_better"] < 0.05
    assert "below the random walk" in gate.reasons[0]


def test_the_random_walk_itself_is_never_promoted() -> None:
    gate = run_gate(TRENDING, RandomWalkForecaster(), RandomWalkForecaster(), CONFIG)
    assert not gate.passed
    assert any("not at least 2% below the random walk" in r for r in gate.reasons)
    assert any("Diebold-Mariano" in r for r in gate.reasons)


def test_a_model_that_chases_noise_is_blocked() -> None:
    """On a driftless series a 3-bar drift estimate is pure noise: worse than the random walk."""
    gate = run_gate(DRIFTLESS, DriftForecaster(window=3), RandomWalkForecaster(), CONFIG)
    assert not gate.passed
    assert gate.candidate["crps"] > gate.random_walk["crps"]
    assert gate.reasons and all("random walk" in r or "base model" in r for r in gate.reasons)


def test_a_model_worse_than_the_one_it_was_tuned_from_is_blocked() -> None:
    gate = run_gate(TRENDING, RandomWalkForecaster(), DriftForecaster(window=60), CONFIG)
    assert not gate.passed
    assert any("worse than the base model" in r for r in gate.reasons)
    assert any("fine-tuning did not help" in r for r in gate.reasons)


def test_too_few_test_origins_never_promotes() -> None:
    thin = FineTuneConfig(symbols=["AAA"], horizon=5, lookback=120, test_window=40, folds=1)
    gate = run_gate(TRENDING, DriftForecaster(window=60), RandomWalkForecaster(), thin)
    assert not gate.passed and gate.n_origins < MIN_ORIGINS
    assert "test origins" in gate.reasons[0]


def test_the_required_improvement_is_enforced() -> None:
    gate = run_gate(
        TRENDING, DriftForecaster(window=60), RandomWalkForecaster(), CONFIG, min_improvement=0.99
    )
    assert not gate.passed
    assert any("99% below" in r for r in gate.reasons)


def test_the_evidence_is_serialisable_and_names_every_comparison() -> None:
    gate = run_gate(TRENDING, DriftForecaster(window=60), RandomWalkForecaster(), CONFIG)
    document = json.loads(json.dumps(gate.to_dict()))
    assert document["passed"] is True and document["n_origins"] == gate.n_origins
    for key in (
        "candidate",
        "baseline",
        "base_model",
        "dm_vs_random_walk",
        "dm_vs_base",
        "reasons",
    ):
        assert key in document
    assert document["baseline"]["model_id"] == "random-walk"


@pytest.mark.parametrize("seed", [21, 22, 23])
def test_a_model_with_no_edge_is_not_promoted_on_any_driftless_series(seed) -> None:
    flat = {"AAA": series(drift=0.0, vol=0.01, seed=seed)}
    gate = run_gate(flat, RandomWalkForecaster(), RandomWalkForecaster(), CONFIG)
    assert not gate.passed
