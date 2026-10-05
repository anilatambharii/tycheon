"""Scoring rules and the finite-sample conformal quantile, checked against closed forms."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from tycheon.calibration.metrics import (
    conformal_index,
    conformal_quantile,
    crps_gaussian,
    crps_samples,
    interpolate_shift,
    interval_coverage,
    pinball,
    pit_histogram,
    quantile_score,
    shift_samples,
)


# ------------------------------------------------------------ conformal quantile
@pytest.mark.parametrize(
    ("n", "level", "expected"),
    [
        (99, 0.99, 99),  # ceil(100 * .99) = 99: just enough scores
        (98, 0.99, None),  # one short: refuse rather than pretend
        (99, 0.01, 1),  # floor(100 * .01) = 1
        (98, 0.01, None),
        (19, 0.95, 19),
        (19, 0.05, 1),
        (9, 0.95, None),
        (10, 0.5, 6),  # ceil(11 * .5)
    ],
)
def test_the_finite_sample_order_statistic(n, level, expected) -> None:
    assert conformal_index(n, level) == expected


def test_a_level_outside_zero_one_is_rejected() -> None:
    for level in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="strictly between"):
            conformal_index(50, level)


def test_the_conformal_quantile_picks_that_order_statistic() -> None:
    scores = np.arange(1.0, 100.0)  # 1..99
    assert conformal_quantile(scores, 0.99) == 99.0
    assert conformal_quantile(scores, 0.01) == 1.0
    assert conformal_quantile(scores[:50], 0.99) is None


def test_central_intervals_cover_at_least_nominal_in_finite_samples() -> None:
    """The reason for the (n+1) correction: coverage >= 1 - alpha for any n, not just large n."""
    rng = np.random.default_rng(0)
    n, trials, alpha = 19, 20000, 0.1
    hits = 0
    for _ in range(trials):
        draws = rng.standard_normal(n + 1)
        scores, y = draws[:n], draws[n]
        lo = conformal_quantile(scores, alpha / 2)
        hi = conformal_quantile(scores, 1 - alpha / 2)
        hits += int(lo <= y <= hi)
    coverage = hits / trials
    assert coverage >= 1 - alpha - 0.01  # at least nominal, up to Monte Carlo error
    assert coverage <= 1 - alpha + 0.03  # and not wildly conservative


# ---------------------------------------------------------------------- pinball
def test_pinball_loss() -> None:
    y, q = np.array([2.0, 0.0]), np.array([1.0, 1.0])
    assert pinball(0.9, q, y).tolist() == pytest.approx([0.9, 0.1])  # under, then over


def test_the_true_quantiles_score_best_on_the_quantile_score() -> None:
    """A proper scoring rule: the true quantile function beats shifted and rescaled ones."""
    rng = np.random.default_rng(1)
    y = rng.standard_normal((4000, 1))
    levels = (0.05, 0.25, 0.5, 0.75, 0.95)
    z = stats.norm.ppf(levels)

    def score(scale=1.0, shift=0.0):
        logq = np.broadcast_to((scale * z + shift)[None, :, None], (4000, 5, 1))
        return float(quantile_score(levels, logq, y).mean())

    truth = score()
    assert truth < score(scale=0.5)
    assert truth < score(scale=2.0)
    assert truth < score(shift=0.3)


def test_interval_coverage() -> None:
    assert interval_coverage(
        np.array([0.0, 0, 0]), np.array([1.0, 1, 1]), np.array([0.5, 2.0, -1])
    ) == pytest.approx(1 / 3)


# ------------------------------------------------------------------------- CRPS
def test_gaussian_crps_closed_form_known_values() -> None:
    # CRPS of N(0,1) at y=0 is (sqrt(2) - 1) / sqrt(pi)
    assert float(crps_gaussian(0.0, 1.0, 0.0)) == pytest.approx((np.sqrt(2) - 1) / np.sqrt(np.pi))
    assert float(crps_gaussian(0.0, 2.0, 0.0)) == pytest.approx(
        2 * (np.sqrt(2) - 1) / np.sqrt(np.pi)
    )


@pytest.mark.parametrize("y", [-2.0, -0.5, 0.0, 1.0, 3.0])
def test_ensemble_crps_matches_the_closed_form(y) -> None:
    rng = np.random.default_rng(2)
    samples = rng.standard_normal((1, 6000, 1))
    estimate = float(crps_samples(samples, np.array([[y]]))[0, 0])
    assert estimate == pytest.approx(float(crps_gaussian(0.0, 1.0, y)), abs=0.02)


def test_crps_prefers_the_right_distribution() -> None:
    rng = np.random.default_rng(3)
    y = rng.standard_normal((500, 1))
    right = rng.standard_normal((500, 80, 1))
    too_narrow = 0.3 * rng.standard_normal((500, 80, 1))
    biased = rng.standard_normal((500, 80, 1)) + 1.0
    assert crps_samples(right, y).mean() < crps_samples(too_narrow, y).mean()
    assert crps_samples(right, y).mean() < crps_samples(biased, y).mean()


def test_fair_crps_does_not_depend_on_the_ensemble_size() -> None:
    rng = np.random.default_rng(4)
    y = rng.standard_normal((4000, 1))
    small = crps_samples(rng.standard_normal((4000, 5, 1)), y).mean()
    large = crps_samples(rng.standard_normal((4000, 100, 1)), y).mean()
    unfair_small = crps_samples(rng.standard_normal((4000, 5, 1)), y, fair=False).mean()
    assert small == pytest.approx(large, abs=0.02)
    assert unfair_small > large + 0.02  # the biased form punishes small ensembles


# -------------------------------------------------------------------------- PIT
def test_pit_is_flat_when_calibrated_and_skewed_when_not() -> None:
    rng = np.random.default_rng(5)
    n = 5000
    truth = rng.standard_normal(n)
    calibrated = stats.norm.cdf(truth)
    too_narrow = stats.norm.cdf(truth / 0.5)  # forecast sigma half the truth: U-shaped PIT
    flat = pit_histogram(calibrated, 10)
    assert flat.sum() == n
    assert flat.min() > 0.8 * n / 10
    assert flat.max() < 1.2 * n / 10
    ushape = pit_histogram(too_narrow, 10)
    assert ushape[0] + ushape[-1] > 0.3 * n


# ------------------------------------------------------------- shifting samples
def test_a_constant_shift_moves_every_sample_and_keeps_order() -> None:
    rng = np.random.default_rng(6)
    samples = rng.standard_normal((200, 3))
    levels = (0.1, 0.5, 0.9)
    out = shift_samples(samples, levels, np.full((3, 3), 0.25))
    np.testing.assert_allclose(out, samples + 0.25)


def test_shifting_preserves_each_steps_ranking_and_cross_step_dependence() -> None:
    rng = np.random.default_rng(7)
    base = rng.standard_normal((400, 1))
    samples = np.hstack(
        [base, base + 0.1 * rng.standard_normal((400, 1))]
    )  # strongly dependent steps
    levels = (0.05, 0.5, 0.95)
    delta = np.array([[-0.5, -0.5], [0.0, 0.0], [0.5, 0.5]])  # widen both tails
    out = shift_samples(samples, levels, delta)
    for step in range(2):
        assert (np.argsort(out[:, step]) == np.argsort(samples[:, step])).mean() > 0.97
    assert np.corrcoef(out[:, 0], out[:, 1])[0, 1] > 0.9  # dependence survives
    assert out[:, 0].std() > samples[:, 0].std()  # the tails widened


def test_shift_never_inverts_the_order_within_a_step() -> None:
    rng = np.random.default_rng(8)
    samples = np.sort(rng.standard_normal((300, 1)), axis=0)
    levels = (0.1, 0.5, 0.9)
    jagged = np.array([[0.5], [-0.5], [0.5]])  # would invert the order without the repair
    out = shift_samples(samples, levels, jagged)
    assert (np.diff(out[:, 0]) >= -1e-12).all()


def test_interpolated_shift_is_clamped_at_the_ends() -> None:
    out = interpolate_shift(np.array([0.0, 0.1, 0.3, 0.5, 1.0]), (0.1, 0.5), np.array([1.0, 3.0]))
    assert out.tolist() == pytest.approx([1.0, 1.0, 2.0, 3.0, 3.0])
