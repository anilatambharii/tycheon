"""Risk measures checked against closed-form results."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from tycheon.errors import ModelError
from tycheon.models.base import CalibrationInfo
from tycheon.models.timesfm import TimesFMForecaster
from tycheon.risk import (
    RiskInput,
    drawdown_probabilities,
    loss_probabilities,
    max_drawdown,
    value_at_risk_and_es,
    volatility_forecast,
)
from tycheon.risk.measures import wilson_interval

AS_OF = pd.Timestamp("2024-01-10", tz="UTC")
START = 100.0


def make_input(paths: np.ndarray, **kwargs) -> RiskInput:
    horizon = paths.shape[1]
    defaults = {
        "name": "test",
        "start_value": START,
        "index": pd.date_range(AS_OF + pd.Timedelta("1D"), periods=horizon, freq="1D"),
        "as_of": AS_OF,
        "calibration_status": "uncalibrated",
        "calibration": None,
        "model_mix": {"test": 1.0},
    }
    defaults.update(kwargs)
    return RiskInput(paths=paths, **defaults)


def gaussian_returns(n: int, mu: float, sigma: float, seed: int = 0) -> RiskInput:
    r = np.random.default_rng(seed).normal(mu, sigma, size=(n, 1))
    return make_input(START * (1.0 + r))


def by_kind(measures):
    return {(m.kind, m.level): m for m in measures}


# ----------------------------------------------------- VaR / ES, Gaussian returns
@pytest.mark.parametrize("level", [0.9, 0.95, 0.975, 0.99])
def test_var_and_es_match_the_gaussian_closed_form(level) -> None:
    mu, sigma = 0.004, 0.03
    inp = gaussian_returns(400_000, mu, sigma)
    z = stats.norm.ppf(1.0 - level)
    var_true = -(mu + sigma * z)
    es_true = -mu + sigma * stats.norm.pdf(z) / (1.0 - level)
    m = by_kind(value_at_risk_and_es(inp, (level,), n_boot=50))
    assert m["VaR", level].value == pytest.approx(var_true, rel=0.02)
    assert m["ES", level].value == pytest.approx(es_true, rel=0.02)
    assert abs(m["VaR", level].value - var_true) < 5 * m["VaR", level].se + 1e-4
    assert abs(m["ES", level].value - es_true) < 5 * m["ES", level].se + 1e-4


@pytest.mark.parametrize("level", [0.9, 0.95, 0.99])
def test_var_and_es_match_the_lognormal_closed_form(level) -> None:
    """Price paths from geometric Brownian motion: the exact tail of exp(X) - 1."""
    m_, s_ = 0.01, 0.06
    x = np.random.default_rng(1).normal(m_, s_, size=(400_000, 1))
    inp = make_input(START * np.exp(x))
    p = 1.0 - level
    xq = m_ + s_ * stats.norm.ppf(p)
    var_true = 1.0 - math.exp(xq)
    es_true = 1.0 - math.exp(m_ + s_**2 / 2) * stats.norm.cdf((xq - m_ - s_**2) / s_) / p
    got = by_kind(value_at_risk_and_es(inp, (level,), n_boot=50))
    assert got["VaR", level].value == pytest.approx(var_true, rel=0.02)
    assert got["ES", level].value == pytest.approx(es_true, rel=0.02)


def test_es_is_at_least_var_and_both_rise_with_the_level() -> None:
    inp = gaussian_returns(50_000, 0.0, 0.02, seed=2)
    ms = value_at_risk_and_es(inp, (0.9, 0.95, 0.975, 0.99), n_boot=20)
    var = [m.value for m in ms if m.kind == "VaR"]
    es = [m.value for m in ms if m.kind == "ES"]
    assert var == sorted(var) and es == sorted(es)
    assert all(e >= v for e, v in zip(es, var, strict=True))


def test_var_scales_with_the_square_root_of_the_horizon() -> None:
    rng = np.random.default_rng(3)
    steps = rng.normal(0.0, 0.01, size=(200_000, 16))
    inp = make_input(START * (1.0 + np.cumsum(steps, axis=1)))
    v1 = by_kind(value_at_risk_and_es(inp, (0.95,), step=0, n_boot=10))["VaR", 0.95].value
    v4 = by_kind(value_at_risk_and_es(inp, (0.95,), step=3, n_boot=10))["VaR", 0.95].value
    v16 = by_kind(value_at_risk_and_es(inp, (0.95,), step=15, n_boot=10))["VaR", 0.95].value
    assert v4 / v1 == pytest.approx(2.0, rel=0.03)
    assert v16 / v1 == pytest.approx(4.0, rel=0.03)


def test_a_loss_is_positive_and_a_gain_distribution_has_negative_var() -> None:
    gains = make_input(START * (1.0 + np.random.default_rng(4).uniform(0.05, 0.15, size=(5000, 1))))
    assert by_kind(value_at_risk_and_es(gains, (0.95,), n_boot=10))["VaR", 0.95].value < 0


def test_a_tail_with_too_few_paths_is_flagged_unreliable() -> None:
    small = gaussian_returns(60, 0.0, 0.02, seed=5)
    m = by_kind(value_at_risk_and_es(small, (0.9, 0.99), n_boot=10))
    assert m["VaR", 0.99].n_tail == 1 and not m["VaR", 0.99].reliable
    assert m["ES", 0.9].n_tail == 6 and not m["ES", 0.9].reliable
    big = gaussian_returns(5000, 0.0, 0.02, seed=5)
    assert by_kind(value_at_risk_and_es(big, (0.95,), n_boot=10))["ES", 0.95].reliable


def test_levels_must_be_confidence_levels() -> None:
    inp = gaussian_returns(100, 0.0, 0.02)
    for bad in ((0.5,), (0.3,), (1.0,)):
        with pytest.raises(ValueError, match="confidence levels"):
            value_at_risk_and_es(inp, bad)


def test_the_measures_are_reproducible_and_serialisable() -> None:
    inp = gaussian_returns(2000, 0.0, 0.02, seed=6)
    a = value_at_risk_and_es(inp, (0.95,), seed=1)
    b = value_at_risk_and_es(inp, (0.95,), seed=1)
    assert [m.se for m in a] == [m.se for m in b]
    assert set(a[0].to_dict()) == {
        "kind",
        "level",
        "step",
        "loss_fraction",
        "standard_error",
        "n_tail_paths",
        "reliable",
        "calibrated",
    }


# --------------------------------------------------------- the calibrated flag
def _calibrated_input(levels=(0.025, 0.05, 0.5, 0.95, 0.975)) -> RiskInput:
    info = CalibrationInfo(
        method="split-conformal", n_scores=100, scores_as_of=AS_OF - pd.Timedelta("1D"),
        holdout_n=25, holdout_coverage={0.9: 0.9}, raw_holdout_coverage={0.9: 0.6},
        tolerance=0.05, calibrated_levels=levels,
    )  # fmt: skip
    r = np.random.default_rng(7).normal(0, 0.02, size=(5000, 1))
    return make_input(START * (1 + r), calibration_status="calibrated", calibration=info)


def test_a_tail_is_only_labelled_calibrated_inside_the_calibrated_range() -> None:
    m = by_kind(value_at_risk_and_es(_calibrated_input(), (0.9, 0.95, 0.975, 0.99), n_boot=10))
    assert m["VaR", 0.95].calibrated and m["ES", 0.975].calibrated
    assert not m["VaR", 0.99].calibrated, "a 99% VaR is not calibrated just because 95% is"


def test_an_uncalibrated_or_stale_input_never_reports_calibrated_measures() -> None:
    plain = gaussian_returns(5000, 0.0, 0.02)
    assert not any(m.calibrated for m in value_at_risk_and_es(plain, (0.95,), n_boot=10))
    stale = _calibrated_input()
    object.__setattr__(stale, "calibration_status", "stale")
    assert not stale.tail_is_calibrated(0.05)


# ------------------------------------------------------------ path requirements
def test_quantile_only_forecasts_are_refused(known_bars, fake_timesfm) -> None:
    history = known_bars.iloc[:300]
    dist = TimesFMForecaster(engine=fake_timesfm).predict(
        history, 5, 1, history["available_at"].iloc[-1]
    )
    with pytest.raises(ModelError, match="joint sample paths"):
        RiskInput.from_forecast(dist)


def test_risk_input_from_a_forecast_carries_its_provenance(known_bars, gaussian_cls) -> None:
    history = known_bars.iloc[:300]
    as_of = history["available_at"].iloc[-1]
    dist = gaussian_cls("g", 0.012).predict(history, 5, 100, as_of)
    inp = RiskInput.from_forecast(dist)
    assert inp.n_paths == 100 and inp.horizon == 5
    assert inp.start_value == dist.last_close and inp.as_of == dist.as_of
    assert inp.calibration_status == "uncalibrated" and inp.model_mix == {"g": 1.0}


def test_a_malformed_input_is_rejected() -> None:
    with pytest.raises(ValueError, match="paths must be"):
        make_input(np.ones((10, 3)), index=pd.date_range(AS_OF, periods=2, freq="1D", tz="UTC"))
    with pytest.raises(ValueError, match="start_value"):
        make_input(np.ones((10, 2)), start_value=0.0)


# ----------------------------------------------------------------------- drawdown
def test_max_drawdown_of_simple_paths() -> None:
    paths = np.array(
        [
            [110.0, 120.0, 130.0],  # only rises: no drawdown
            [90.0, 70.0, 50.0],  # falls 50% from the starting peak
            [120.0, 60.0, 90.0],  # peaks at 120, troughs at 60: 50%
            [100.0, 100.0, 100.0],  # flat
        ]
    )
    np.testing.assert_allclose(max_drawdown(make_input(paths)), [0.0, 0.5, 0.5, 0.0])


def test_the_starting_value_counts_as_a_peak() -> None:
    assert max_drawdown(make_input(np.array([[80.0, 85.0, 90.0]])))[0] == pytest.approx(0.2)


def test_drawdown_probability_is_the_share_of_paths_that_exceed_the_threshold() -> None:
    paths = np.vstack(
        [np.full((50, 3), 110.0), np.tile([95.0, 80.0, 70.0], (50, 1))]
    )  # half fall 30%
    p = {e.threshold: e for e in drawdown_probabilities(make_input(paths), (0.05, 0.2, 0.31))}
    assert p[0.05].probability == pytest.approx(0.5)
    assert p[0.2].probability == pytest.approx(0.5)
    assert p[0.31].probability == 0.0
    assert p[0.2].low < 0.5 < p[0.2].high


def test_drawdown_probability_falls_as_the_threshold_rises() -> None:
    rng = np.random.default_rng(8)
    inp = make_input(START * np.exp(np.cumsum(rng.normal(0, 0.02, size=(4000, 20)), axis=1)))
    probs = [e.probability for e in drawdown_probabilities(inp, (0.02, 0.05, 0.1, 0.2))]
    assert probs == sorted(probs, reverse=True)
    assert all(0.0 <= p <= 1.0 for p in probs)


def test_expected_max_drawdown_of_brownian_motion_matches_theory() -> None:
    """E[max drawdown] of driftless Brownian motion is sqrt(pi / 2) * sigma * sqrt(T).

    A path sampled at ``steps`` points under-reads each of the continuous maximum and minimum
    by about 0.5826 * sigma * sqrt(dt) (the Broadie-Glasserman-Kou continuity correction), so
    the discrete expectation is checked against the corrected value.
    """
    sigma_total, steps, n = 0.02, 1500, 4000
    rng = np.random.default_rng(9)
    inc = rng.normal(0.0, sigma_total / math.sqrt(steps), size=(n, steps))
    inp = make_input(START * np.exp(np.cumsum(inc, axis=1)))
    mean_dd = float(max_drawdown(inp).mean())
    continuous = math.sqrt(math.pi / 2.0) * sigma_total
    corrected = continuous - 2 * 0.5826 * sigma_total / math.sqrt(steps)
    assert mean_dd == pytest.approx(corrected, rel=0.03)
    assert mean_dd < continuous  # and it really is below the continuous-time value


def test_loss_probability_matches_the_gaussian_tail() -> None:
    sigma = 0.04
    inp = gaussian_returns(400_000, 0.0, sigma, seed=10)
    got = {e.threshold: e for e in loss_probabilities(inp, (0.04, 0.08))}
    assert got[0.04].probability == pytest.approx(stats.norm.cdf(-0.04 / sigma), abs=0.004)
    assert got[0.08].probability == pytest.approx(stats.norm.cdf(-0.08 / sigma), abs=0.002)
    assert got[0.04].to_dict()["n_paths"] == 400_000


def test_wilson_interval_behaves_at_the_edges() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(0, 100)
    assert lo == 0.0 and 0 < hi < 0.05
    lo, hi = wilson_interval(100, 100)
    assert hi == pytest.approx(1.0) and 0.95 < lo < 1
    lo, hi = wilson_interval(50, 100)
    assert lo < 0.5 < hi and hi - lo < 0.21


# --------------------------------------------------------------------- volatility
def test_volatility_forecast_recovers_the_generating_volatility() -> None:
    sigma, h = 0.01, 20
    rng = np.random.default_rng(11)
    inp = make_input(START * np.exp(np.cumsum(rng.normal(0, sigma, size=(20000, h)), axis=1)))
    vol = volatility_forecast(inp)
    assert vol.horizon_vol_per_bar == pytest.approx(sigma, rel=0.02)
    assert vol.annualized == pytest.approx(sigma * math.sqrt(252), rel=0.02)
    q = vol.path_realized_vol_quantiles
    assert q[0.05] < sigma < q[0.95]
    assert q[0.5] == pytest.approx(sigma, rel=0.05)


def test_volatility_uses_the_models_own_sigma_when_given() -> None:
    inp = make_input(
        START * np.exp(np.cumsum(np.random.default_rng(12).normal(0, 0.01, size=(500, 4)), axis=1))
    )
    vol = volatility_forecast(
        inp, model_sigma=np.array([0.02, 0.02, 0.02, 0.02]), annualization=365
    )
    assert vol.model_sigma_per_bar == pytest.approx(0.02)
    assert vol.annualization == 365
    json_ready = vol.to_dict()
    assert json_ready["model_sigma_per_bar"] == pytest.approx(0.02)


def test_a_single_step_horizon_has_no_per_path_volatility() -> None:
    vol = volatility_forecast(gaussian_returns(500, 0.0, 0.02))
    assert vol.path_realized_vol_quantiles == {}
    assert vol.horizon_vol_per_bar == pytest.approx(0.02, rel=0.1)
