"""Metrics against hand-computed values and known-distribution truths."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from tycheon.backtest.metrics import (
    diebold_mariano,
    directional_accuracy,
    evaluate,
    forecast_variance,
    information_coefficient,
    interval_stats,
    mae,
    mase,
    overlap_lags,
    qlike,
    realized_variance,
    rmse,
)
from tycheon.calibration.scores import ScoreSet

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
H = 2


def make_scores(
    median: np.ndarray,
    realized_end: np.ndarray,
    *,
    sigma: float = 0.02,
    model_id: str = "m",
    samples: bool = False,
    stride: int = H,
    seed: int = 0,
) -> ScoreSet:
    """A ScoreSet whose forecasts are Gaussian around ``median`` (end of horizon)."""
    n = len(median)
    z = stats.norm.ppf(LEVELS)
    base = np.empty((n, len(LEVELS), H))
    for h in range(H):
        scale = (h + 1) / H
        base[:, :, h] = (median[:, None] * scale) + sigma * scale * z[None, :]
    realized = np.stack([realized_end * (h + 1) / H for h in range(H)], axis=1)
    rng = np.random.default_rng(seed)
    paths = None
    if samples:
        paths = np.stack(
            [
                median[:, None] * ((np.arange(H) + 1) / H)
                + sigma * ((np.arange(H) + 1) / H) * rng.standard_normal((n, H))
                for _ in range(200)
            ],
            axis=1,
        )
    origins = pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC")
    return ScoreSet(
        model_id=model_id, horizon=H, levels=LEVELS, origin_times=origins,
        outcome_times=np.tile(np.arange(H, dtype=np.int64), (n, 1)) + 10**18,
        base_logq=base, realized=realized, samples=paths, n_samples=200 if samples else 0,
        stride=stride,
    )  # fmt: skip


# ------------------------------------------------------------------------- point
def test_mae_rmse_mase_by_hand() -> None:
    median = np.array([0.01, -0.02, 0.0, 0.03])
    real = np.array([0.02, -0.01, 0.01, 0.0])
    s = make_scores(median, real)
    errors = np.abs(median - real)
    assert mae(s) == pytest.approx(errors.mean())
    assert rmse(s) == pytest.approx(math.sqrt(np.mean(errors**2)))
    assert mase(s) == pytest.approx(errors.mean() / np.abs(real).mean())


def test_a_zero_forecast_has_mase_one() -> None:
    real = np.random.default_rng(0).normal(0, 0.02, 100)
    assert mase(make_scores(np.zeros(100), real)) == pytest.approx(1.0)


def test_a_perfect_forecast_has_mase_zero() -> None:
    real = np.random.default_rng(1).normal(0, 0.02, 50)
    assert mase(make_scores(real.copy(), real)) == pytest.approx(0.0, abs=1e-12)


def test_directional_accuracy_ignores_flat_calls() -> None:
    median = np.array([0.01, -0.01, 0.0, 0.02, -0.02])
    real = np.array([0.02, 0.01, 0.03, 0.01, -0.01])
    # calls: +,-,flat,+,-  vs outcomes: +,+,+,+,-  -> 3 of the 4 non-flat calls are right
    assert directional_accuracy(make_scores(median, real)) == pytest.approx(3 / 4)


def test_ic_is_one_for_a_perfect_ranking_and_nan_for_a_constant_forecast() -> None:
    real = np.linspace(-0.03, 0.03, 40)
    assert information_coefficient(make_scores(real * 0.5, real)) == pytest.approx(1.0)
    assert information_coefficient(make_scores(real * 0.5, real), rank=True) == pytest.approx(1.0)
    assert math.isnan(information_coefficient(make_scores(np.zeros(40), real)))


def test_rank_ic_is_robust_to_a_monotone_transform() -> None:
    rng = np.random.default_rng(2)
    real = rng.normal(0, 0.02, 200)
    pred = real + rng.normal(0, 0.02, 200)
    plain = information_coefficient(make_scores(pred, real), rank=True)
    warped = information_coefficient(make_scores(np.sign(pred) * pred**2 * 50, real), rank=True)
    assert plain == pytest.approx(warped)


# ------------------------------------------------------------------ distribution
def test_interval_coverage_of_a_calibrated_gaussian_is_nominal() -> None:
    rng = np.random.default_rng(3)
    n, sigma = 20000, 0.02
    median = np.zeros(n)
    real = rng.normal(0, sigma, n)
    s = make_scores(median, real, sigma=sigma)
    cov, width = interval_stats(s, 0.5)  # type: ignore[misc]
    assert cov == pytest.approx(0.5, abs=0.015)
    assert width == pytest.approx(2 * stats.norm.ppf(0.75) * sigma, rel=1e-6)


def test_an_unsupported_coverage_is_none() -> None:
    s = make_scores(np.zeros(10), np.zeros(10))
    assert interval_stats(s, 0.99) is None


def test_exact_crps_is_flagged_and_the_grid_approximation_is_close() -> None:
    rng = np.random.default_rng(4)
    n, sigma = 3000, 0.02
    real = rng.normal(0, sigma, n)
    exact = make_scores(np.zeros(n), real, sigma=sigma, samples=True)
    grid = make_scores(np.zeros(n), real, sigma=sigma, samples=False)
    wrong = evaluate(grid, grid)
    right = evaluate(exact, exact)
    assert right.crps_approximate is False and wrong.crps_approximate is True
    closed_form = sigma * (1 / math.sqrt(math.pi))  # E[CRPS] of N(0, s) forecasting N(0, s)
    assert right.crps == pytest.approx(closed_form, rel=0.06)
    assert wrong.crps == pytest.approx(closed_form, rel=0.12)


# ------------------------------------------------------------------------- QLIKE
def test_qlike_is_zero_when_the_forecast_equals_the_realised_variance() -> None:
    v = np.array([1e-4, 2e-4, 3e-4])
    assert qlike(v, v) == pytest.approx(0.0)


def test_qlike_penalises_under_prediction_more_than_over_prediction() -> None:
    v = np.full(10, 1e-4)
    assert qlike(v / 2, v) > qlike(v * 2, v)


def test_qlike_is_minimised_at_the_true_variance_in_expectation() -> None:
    rng = np.random.default_rng(5)
    true_var = 4e-4
    realized = true_var * rng.chisquare(5, 50000) / 5
    grid = true_var * np.array([0.5, 0.8, 1.0, 1.25, 2.0])
    losses = [qlike(np.full(len(realized), f), realized) for f in grid]
    assert int(np.argmin(losses)) == 2


def test_qlike_drops_non_positive_inputs() -> None:
    assert math.isnan(qlike(np.array([0.0]), np.array([1e-4])))


def test_forecast_and_realised_variance_from_a_scoreset() -> None:
    s = make_scores(np.zeros(500), np.random.default_rng(6).normal(0, 0.02, 500), samples=True)
    fv = forecast_variance(s)
    assert fv is not None and fv.shape == (500,)
    assert forecast_variance(make_scores(np.zeros(5), np.zeros(5))) is None
    rv = realized_variance(s)
    steps = np.diff(np.concatenate([np.zeros((500, 1)), s.realized], axis=1), axis=1)
    assert rv == pytest.approx((steps**2).sum(axis=1))


# --------------------------------------------------------------- Diebold-Mariano
def test_dm_on_identical_losses_finds_no_difference() -> None:
    loss = np.random.default_rng(7).random(50)
    res = diebold_mariano(loss, loss)
    assert res.p_two_sided == 1.0 and res.mean_diff == 0.0


def test_dm_detects_a_clearly_better_model_and_signs_it_correctly() -> None:
    rng = np.random.default_rng(8)
    bench = rng.random(200)
    model = bench - 0.2 + rng.normal(0, 0.05, 200)
    res = diebold_mariano(model, bench)
    assert res.mean_diff < 0 and res.statistic < 0
    assert res.p_model_better < 1e-6 and res.p_two_sided < 1e-6
    flipped = diebold_mariano(bench, model)
    assert flipped.p_model_better > 0.999999


def test_dm_has_the_right_size_under_the_null() -> None:
    rng = np.random.default_rng(9)
    rejections = sum(
        diebold_mariano(rng.normal(size=100), rng.normal(size=100)).p_two_sided < 0.05
        for _ in range(1500)
    )
    assert rejections / 1500 == pytest.approx(0.05, abs=0.02)


def test_dm_matches_a_reference_t_test_when_there_is_no_serial_correlation() -> None:
    rng = np.random.default_rng(10)
    a, b = rng.normal(0.1, 1, 80), rng.normal(0, 1, 80)
    d = a - b
    res = diebold_mariano(a, b)
    n = len(d)
    hln = math.sqrt((n + 1 - 2 + 0) / n)
    stat = d.mean() / math.sqrt(d.var(ddof=0) / n) * hln
    assert res.statistic == pytest.approx(stat)
    assert res.p_two_sided == pytest.approx(2 * stats.t(n - 1).sf(abs(stat)))


def test_overlapping_forecasts_need_lags_or_the_test_over_rejects() -> None:
    """Losses of 5-step forecasts at every bar are MA(4): ignoring that inflates the size."""
    rng = np.random.default_rng(11)
    naive = corrected = 0
    reps = 600
    for _ in range(reps):
        e = rng.normal(size=204)
        d = np.convolve(e, np.ones(5), mode="valid")  # MA(4), mean zero
        naive += diebold_mariano(d, np.zeros_like(d), lags=0).p_two_sided < 0.05
        corrected += diebold_mariano(d, np.zeros_like(d), lags=4).p_two_sided < 0.05
    assert naive / reps > 0.15
    assert corrected / reps < 0.09


def test_dm_with_too_few_origins_is_nan_not_a_verdict() -> None:
    res = diebold_mariano(np.array([1.0, 2.0]), np.array([1.5, 1.0]))
    assert math.isnan(res.p_two_sided)


@pytest.mark.parametrize(("h", "stride", "lags"), [(5, 5, 0), (5, 1, 4), (5, 2, 2), (1, 1, 0)])
def test_overlap_lags(h: int, stride: int, lags: int) -> None:
    assert overlap_lags(h, stride) == lags


# ---------------------------------------------------------------------- evaluate
def test_evaluate_reads_a_better_model_against_the_random_walk() -> None:
    rng = np.random.default_rng(12)
    n = 300
    signal = rng.normal(0, 0.02, n)
    real = signal + rng.normal(0, 0.005, n)
    rw = make_scores(np.zeros(n), real, model_id="random-walk")
    good = make_scores(signal, real, model_id="good", samples=False)
    ev = evaluate(good, rw)
    assert ev.verdict == "beats the random walk"
    assert ev.mase < 0.5 and ev.ic > 0.9
    assert evaluate(rw, rw).verdict == "benchmark"


def test_evaluate_says_when_a_model_is_indistinguishable_or_worse() -> None:
    rng = np.random.default_rng(13)
    n = 300
    real = rng.normal(0, 0.02, n)
    rw = make_scores(np.zeros(n), real, model_id="random-walk")
    noise = make_scores(rng.normal(0, 0.002, n), real, model_id="noise")
    assert evaluate(noise, rw).verdict == "indistinguishable from the random walk"
    bad = make_scores(rng.normal(0, 0.03, n), real, model_id="bad")
    assert evaluate(bad, rw).verdict == "worse than the random walk"


def test_evaluate_refuses_different_origins() -> None:
    a = make_scores(np.zeros(30), np.zeros(30), model_id="a")
    b = make_scores(np.zeros(31), np.zeros(31), model_id="b")
    with pytest.raises(ValueError, match="same origins"):
        evaluate(a, b)


def test_evaluate_does_not_mix_exact_and_approximate_crps() -> None:
    rng = np.random.default_rng(14)
    real = rng.normal(0, 0.02, 200)
    with_paths = make_scores(np.zeros(200), real, model_id="paths", samples=True)
    quantile_only = make_scores(np.zeros(200), real, model_id="random-walk")
    ev = evaluate(with_paths, quantile_only)
    assert ev.crps_approximate is True  # both fell back to the same quantile-grid measure
    assert ev.to_dict()["verdict"] in {
        "indistinguishable from the random walk",
        "beats the random walk",
        "worse than the random walk",
    }
