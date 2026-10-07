"""Outcome metrics: correct values, the registration, and an honest statement of its limits."""

from __future__ import annotations

import importlib
import math
from importlib import metadata
from pathlib import Path

import numpy as np
import pytest
from tests.test_backtest_metrics import make_scores

from tycheon.backtest.metrics import crps_per_origin, interval_stats, mase
from tycheon.metrics import (
    CRPS,
    QLIKE,
    REGISTERED,
    DieboldMarianoVsRandomWalk,
    IntervalCoverage,
    MASEvsRandomWalk,
    MetricResult,
    OutcomeMetric,
)

GROUP = "keelgate.outcome_metrics"


@pytest.fixture(scope="module")
def pair():
    rng = np.random.default_rng(21)
    n = 300
    signal = rng.normal(0, 0.02, n)
    real = signal + rng.normal(0, 0.005, n)
    rw = make_scores(np.zeros(n), real, model_id="random-walk")
    good = make_scores(signal, real, model_id="good", samples=True)
    return good, rw


def test_the_registered_entry_points_resolve_to_the_metric_classes() -> None:
    eps = {ep.name: ep for ep in metadata.entry_points(group=GROUP)}
    assert set(eps) == set(REGISTERED), "pyproject and tycheon.metrics.REGISTERED disagree"
    for name, ep in eps.items():
        module, _, attribute = ep.value.partition(":")
        assert getattr(importlib.import_module(module), attribute) is REGISTERED[name]


@pytest.mark.parametrize("metric", [CRPS(), IntervalCoverage(), MASEvsRandomWalk(), QLIKE()])
def test_metrics_follow_the_local_protocol(metric) -> None:
    assert isinstance(metric, OutcomeMetric)
    assert metric.name and isinstance(metric.higher_is_better, bool)


def test_crps_matches_the_evaluation_code(pair) -> None:
    good, _ = pair
    result = CRPS().compute(good)
    assert result.value == pytest.approx(float(crps_per_origin(good)[0].mean()))
    assert result.details["approximate"] == 0  # the model has sample paths


def test_coverage_reports_the_gap_to_nominal(pair) -> None:
    good, _ = pair
    result = IntervalCoverage(0.9).compute(good)
    coverage, width = interval_stats(good, 0.9)  # type: ignore[misc]
    assert result.name == "coverage_90"
    assert result.value == pytest.approx(coverage)
    assert result.details["gap"] == pytest.approx(abs(coverage - 0.9))
    assert result.details["mean_width"] == pytest.approx(width)


def test_coverage_of_an_unrecorded_level_is_none_not_a_guess(pair) -> None:
    good, _ = pair
    assert IntervalCoverage(0.99).compute(good).value is None


@pytest.mark.parametrize("level", [0.0, 1.0, -0.1, 1.5])
def test_coverage_rejects_an_impossible_level(level) -> None:
    with pytest.raises(ValueError):
        IntervalCoverage(level)


def test_mase_matches_and_a_better_model_beats_the_walk(pair) -> None:
    good, _ = pair
    result = MASEvsRandomWalk().compute(good)
    assert result.value == pytest.approx(mase(good)) and result.value < 1.0


def test_diebold_mariano_needs_the_benchmark_and_detects_skill(pair) -> None:
    good, rw = pair
    with pytest.raises(ValueError, match="random walk as benchmark"):
        DieboldMarianoVsRandomWalk().compute(good)
    for loss in ("squared_error", "crps"):
        result = DieboldMarianoVsRandomWalk(loss).compute(good, rw)
        assert result.name == f"dm_p_{loss}"
        assert result.value is not None and result.value < 0.01
        assert result.details["n"] == good.n
    with pytest.raises(ValueError):
        DieboldMarianoVsRandomWalk("mae")


def test_qlike_needs_sample_paths(pair) -> None:
    good, rw = pair
    assert QLIKE().compute(good).value is not None
    assert QLIKE().compute(rw).value is None
    assert "no sample paths" in str(QLIKE().compute(rw).details["reason"])


def test_a_non_finite_value_becomes_none() -> None:
    n = 5
    degenerate = make_scores(np.zeros(n), np.zeros(n), model_id="flat")
    result = MASEvsRandomWalk().compute(degenerate)
    assert result.value is None or math.isfinite(result.value)
    assert isinstance(result, MetricResult)


def test_the_module_does_not_import_keelgate() -> None:
    """The metrics must stay usable (and discoverable) without the harness installed."""
    source = importlib.import_module("tycheon.metrics").__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")
    assert "import keelgate" not in text and "from keelgate" not in text
