"""Conformal calibration against ground truth: known data, deliberately wrong forecasters.

The data are exactly N(0, 0.012^2) log returns, so a forecaster with a different sigma or a
drift is wrong by a *known* amount. Calibrated intervals must then hit their target coverage
on origins the calibrator never saw.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tycheon.calibration import (
    AdaptiveConformal,
    ConformalCalibrator,
    ScoreSet,
    SplitConformal,
    calibrate,
    collect_scores,
)
from tycheon.errors import LookaheadError, ModelError
from tycheon.models.base import CalibrationInfo
from tycheon.models.timesfm import TimesFMForecaster

H = 5
SIGMA = 0.012
SPLIT_AT = 4500  # bar index separating the calibration period from the later test period


@pytest.fixture(scope="module")
def narrow_model(gaussian_cls):
    """Half the true sigma and a drift of +0.002 a bar: too narrow *and* biased."""
    return gaussian_cls("narrow-biased", sigma=0.5 * SIGMA, drift=0.002)


@pytest.fixture(scope="module")
def as_of_a(known_bars) -> pd.Timestamp:
    return pd.Timestamp(known_bars["available_at"].iloc[SPLIT_AT])


@pytest.fixture(scope="module")
def scores_a(known_bars, narrow_model, as_of_a) -> ScoreSet:
    """Calibration-period scores: everything known at the start of the test period."""
    bars = known_bars.iloc[: SPLIT_AT + 1]
    return collect_scores(
        narrow_model, bars, as_of=as_of_a, horizon=H, n_origins=600, max_history=40, n_samples=80
    )


@pytest.fixture(scope="module")
def scores_b(known_bars, narrow_model) -> ScoreSet:
    """Later scores, never seen by a calibrator fitted on A: an independent test set."""
    return collect_scores(
        narrow_model, known_bars, as_of=known_bars["available_at"].iloc[-1], horizon=H,
        n_origins=600, max_history=40, n_samples=80, seed=1000,
    )  # fmt: skip


def _idx(levels: tuple[float, ...], target: float) -> int:
    return next(i for i, level in enumerate(levels) if abs(level - target) < 1e-9)


def _coverage(
    scores: ScoreSet, delta: np.ndarray, levels: tuple[float, ...], nominal: float
) -> float:
    tail = (1 - nominal) / 2
    lo, hi = _idx(scores.levels, tail), _idx(scores.levels, 1 - tail)
    j_lo, j_hi = _idx(levels, tail), _idx(levels, 1 - tail)
    low = scores.base_logq[:, lo, :] + delta[j_lo][None, :]
    high = scores.base_logq[:, hi, :] + delta[j_hi][None, :]
    return float(((scores.realized >= low) & (scores.realized <= high)).mean())


def test_the_forecaster_really_is_miscalibrated(scores_b) -> None:
    """Guard the guard: if raw coverage were already fine, the tests below would prove nothing."""
    lo, hi = scores_b.levels.index(0.05), scores_b.levels.index(0.95)
    raw = (
        (scores_b.realized >= scores_b.base_logq[:, lo])
        & (scores_b.realized <= scores_b.base_logq[:, hi])
    ).mean()
    assert raw < 0.7


@pytest.mark.parametrize(
    "method", [SplitConformal(), AdaptiveConformal(gamma=0.02)], ids=["split", "aci"]
)
@pytest.mark.parametrize("nominal", [0.5, 0.8, 0.9, 0.95])
def test_calibrated_intervals_hit_target_coverage_on_unseen_origins(
    method, nominal, scores_a, scores_b
) -> None:
    """The headline property: a calibrator fitted on the past covers the future at its target."""
    cal = ConformalCalibrator(method).fit(scores_a)
    adj = cal.adjustment
    achieved = _coverage(scores_b, adj.delta, adj.levels, nominal)
    # 3600 outcomes (600 origins x 5 steps), strongly dependent within an origin: allow about
    # three effective standard errors.
    assert abs(achieved - nominal) < 0.045, f"{method.method} at {nominal}: {achieved:.3f}"


@pytest.mark.parametrize(
    "method", [SplitConformal(), AdaptiveConformal(gamma=0.02)], ids=["split", "aci"]
)
def test_the_calibration_corrects_bias_not_just_width(method, scores_a, scores_b) -> None:
    """A median drifting +0.002 a bar too high puts >50% of outcomes below it; fix it."""
    cal = ConformalCalibrator(method).fit(scores_a)
    adj = cal.adjustment
    j_adj, j_base = adj.levels.index(0.5), scores_b.levels.index(0.5)
    median = scores_b.base_logq[:, j_base, :] + adj.delta[j_adj][None, :]
    below = (scores_b.realized <= median).mean()
    raw_below = (scores_b.realized <= scores_b.base_logq[:, j_base, :]).mean()
    assert abs(below - 0.5) < 0.05
    assert raw_below > 0.58, (
        "a median biased high is exceeded by the outcome less than half the time"
    )


def test_the_holdout_report_and_status_say_calibrated(scores_a) -> None:
    cal = ConformalCalibrator(SplitConformal()).fit(scores_a)
    status, achieved, tol = cal.status()
    assert status == "calibrated"
    assert abs(achieved - 0.9) <= tol
    ninety = cal.report.coverage_at(0.9)
    assert ninety.raw < 0.7 and abs(ninety.calibrated - 0.9) < 0.07
    assert ninety.width_calibrated > ninety.width_raw  # it was too narrow, so it widened
    assert cal.report.n_holdout >= 10
    assert cal.report.quantile_score_calibrated < cal.report.quantile_score_raw
    assert cal.report.crps_calibrated < cal.report.crps_raw


def test_calibrate_returns_a_calibrated_forecast_that_carries_its_evidence(
    known_bars, narrow_model, scores_a, as_of_a
) -> None:
    cal = ConformalCalibrator(SplitConformal()).fit(scores_a)
    history = known_bars.iloc[SPLIT_AT - 39 : SPLIT_AT + 1]
    raw = narrow_model.predict(history, H, 80, as_of_a, seed=3)
    out = cal.calibrate(raw)

    assert raw.calibration_status == "uncalibrated"
    assert out.calibration_status == "calibrated"
    info = out.calibration
    assert isinstance(info, CalibrationInfo)
    assert info.method == "split-conformal"
    assert info.n_scores == 600
    assert info.scores_as_of <= out.as_of
    assert info.holdout_n >= 10
    assert abs(info.holdout_coverage[0.9] - 0.9) <= info.tolerance
    assert info.raw_holdout_coverage[0.9] < 0.7
    assert out.metadata.diagnostics["calibrated_by"] == "split-conformal"
    assert out.summary().count("calibrated") >= 1 and "holdout" in out.summary()
    assert out.to_dict()["calibration"]["method"] == "split-conformal"
    assert out.model_mix == raw.model_mix and out.as_of == raw.as_of


def test_calibrated_forecasts_cover_the_realised_future(known_bars, narrow_model, scores_a) -> None:
    """End to end on fresh forecasts: calibrated 90% intervals vs what actually happened."""
    cal = ConformalCalibrator(SplitConformal()).fit(scores_a)
    close = known_bars["close"].to_numpy()
    available = pd.DatetimeIndex(known_bars["available_at"])
    hits_raw = hits_cal = total = 0
    for i in range(SPLIT_AT + 5, SPLIT_AT + 5 + 5 * 160, 5):
        history = known_bars.iloc[i - 39 : i + 1]
        raw = narrow_model.predict(history, H, 80, available[i], seed=i)
        out = cal.calibrate(raw)
        lo, hi = out.interval(0.9)
        lo_raw, hi_raw = raw.interval(0.9)
        future = close[i + 1 : i + 1 + H]
        hits_cal += int(((future >= lo) & (future <= hi)).sum())
        hits_raw += int(((future >= lo_raw) & (future <= hi_raw)).sum())
        total += H
    assert hits_cal / total > 0.84, f"calibrated {hits_cal / total:.3f}"
    assert hits_raw / total < 0.7, f"raw {hits_raw / total:.3f}"


def test_calibrated_samples_and_quantiles_agree(
    known_bars, narrow_model, scores_a, as_of_a
) -> None:
    cal = ConformalCalibrator(SplitConformal()).fit(scores_a)
    raw = narrow_model.predict(
        known_bars.iloc[SPLIT_AT - 39 : SPLIT_AT + 1], H, 400, as_of_a, seed=9
    )
    out = cal.calibrate(raw)
    for level in (0.05, 0.5, 0.95):
        from_samples = np.quantile(out.samples, level, axis=0)
        np.testing.assert_allclose(out.quantile(level), from_samples, rtol=0.01)


def test_calibration_keeps_dependence_between_steps(
    known_bars, narrow_model, scores_a, as_of_a
) -> None:
    """Risk is path-dependent: shifting marginals must not scramble which paths are the bad ones."""
    cal = ConformalCalibrator(SplitConformal()).fit(scores_a)
    raw = narrow_model.predict(
        known_bars.iloc[SPLIT_AT - 39 : SPLIT_AT + 1], H, 600, as_of_a, seed=9
    )
    out = cal.calibrate(raw)
    rho_raw = pd.Series(raw.samples[:, 0]).corr(pd.Series(raw.samples[:, -1]), method="spearman")
    rho_cal = pd.Series(out.samples[:, 0]).corr(pd.Series(out.samples[:, -1]), method="spearman")
    assert rho_cal == pytest.approx(rho_raw, abs=0.03)


def test_an_exactly_right_forecaster_is_left_nearly_alone(
    known_bars, gaussian_cls, as_of_a
) -> None:
    right = gaussian_cls("right", sigma=SIGMA)
    s = collect_scores(
        right, known_bars.iloc[: SPLIT_AT + 1], as_of=as_of_a, horizon=H, n_origins=600,
        max_history=40, n_samples=80,
    )  # fmt: skip
    cal = ConformalCalibrator(SplitConformal()).fit(s)
    ninety = cal.report.coverage_at(0.9)
    assert abs(ninety.raw - 0.9) < 0.04
    assert 0.9 < ninety.width_calibrated / ninety.width_raw < 1.15
    assert cal.status()[0] == "calibrated"


def test_a_quantile_only_model_is_calibrated_on_the_levels_it_has(
    known_bars, fake_timesfm, as_of_a
) -> None:
    model = TimesFMForecaster(engine=fake_timesfm)
    s = collect_scores(
        model,
        known_bars.iloc[: SPLIT_AT + 1],
        as_of=as_of_a,
        horizon=H,
        n_origins=300,
        max_history=60,
    )
    cal = ConformalCalibrator(SplitConformal()).fit(s)
    raw = model.predict(known_bars.iloc[SPLIT_AT - 59 : SPLIT_AT + 1], H, 1, as_of_a)
    out = cal.calibrate(raw)
    assert out.samples is None, "calibration must not invent sample paths"
    assert out.calibration_status in ("calibrated", "stale")
    assert min(out.quantile_levels) >= 0.1 - 1e-12 and max(out.quantile_levels) <= 0.9 + 1e-12
    assert (np.diff(out.quantiles, axis=0) >= 0).all()


# ------------------------------------------------------------------- honesty rules
def test_too_few_scores_leaves_the_forecast_uncalibrated_and_says_why(
    known_bars, narrow_model, as_of_a
) -> None:
    few = collect_scores(
        narrow_model, known_bars.iloc[: SPLIT_AT + 1], as_of=as_of_a, horizon=H, n_origins=20,
        max_history=40,
    )  # fmt: skip
    cal = ConformalCalibrator(SplitConformal()).fit(few)
    raw = narrow_model.predict(known_bars.iloc[SPLIT_AT - 39 : SPLIT_AT + 1], H, 80, as_of_a)
    out = cal.calibrate(raw)
    assert out.calibration_status == "uncalibrated" and out.calibration is None
    assert "calibration_unavailable" in out.metadata.diagnostics
    np.testing.assert_array_equal(out.quantiles, raw.quantiles)  # not adjusted without evidence


def test_tails_the_data_cannot_support_are_left_uncalibrated_and_noted(
    known_bars, narrow_model, as_of_a
) -> None:
    """A 1% tail needs ~99 scores. With 60 it must be dropped, not faked."""
    s = collect_scores(
        narrow_model, known_bars.iloc[: SPLIT_AT + 1], as_of=as_of_a, horizon=H, n_origins=60,
        max_history=40, n_samples=80,
    )  # fmt: skip
    cal = ConformalCalibrator(SplitConformal(min_scores=20), min_holdout=5).fit(s)
    assert 0.01 not in cal.adjustment.levels and 0.99 not in cal.adjustment.levels
    assert 0.5 in cal.adjustment.levels
    raw = narrow_model.predict(known_bars.iloc[SPLIT_AT - 39 : SPLIT_AT + 1], H, 80, as_of_a)
    out = cal.calibrate(raw)
    assert out.calibration is not None
    assert 0.01 not in out.calibration.calibrated_levels
    assert any("too few scores" in note for note in out.calibration.notes)


@pytest.mark.leakage
def test_calibration_data_published_after_the_forecast_as_of_is_refused(
    known_bars, narrow_model, scores_b, as_of_a
) -> None:
    """Calibrating a forecast made at A with outcomes only known later is the leak to prevent."""
    cal = ConformalCalibrator(SplitConformal()).fit(scores_b)  # outcomes known near the end
    raw = narrow_model.predict(known_bars.iloc[SPLIT_AT - 39 : SPLIT_AT + 1], H, 80, as_of_a)
    with pytest.raises(LookaheadError, match="after this forecast's as_of"):
        cal.calibrate(raw)


def test_calibrating_before_fitting_is_an_error(known_bars, narrow_model, as_of_a) -> None:
    raw = narrow_model.predict(known_bars.iloc[SPLIT_AT - 39 : SPLIT_AT + 1], H, 20, as_of_a)
    with pytest.raises(ModelError, match="fit the calibrator"):
        ConformalCalibrator(SplitConformal()).calibrate(raw)
    with pytest.raises(ModelError, match="fit the calibrator"):
        ConformalCalibrator(SplitConformal()).status()


def test_a_longer_horizon_than_was_calibrated_is_refused(
    known_bars, narrow_model, scores_a, as_of_a
) -> None:
    cal = ConformalCalibrator(SplitConformal()).fit(scores_a)
    raw = narrow_model.predict(known_bars.iloc[SPLIT_AT - 39 : SPLIT_AT + 1], H + 3, 20, as_of_a)
    with pytest.raises(ModelError, match="exceeds the calibrated horizon"):
        cal.calibrate(raw)


def test_a_mismatched_sample_count_is_noted(known_bars, narrow_model, scores_a, as_of_a) -> None:
    cal = ConformalCalibrator(SplitConformal()).fit(scores_a)  # calibrated with 80 samples
    raw = narrow_model.predict(known_bars.iloc[SPLIT_AT - 39 : SPLIT_AT + 1], H, 500, as_of_a)
    out = cal.calibrate(raw)
    assert any("500" in note and "80" in note for note in out.calibration.notes)


def test_a_shorter_horizon_uses_the_first_steps(
    known_bars, narrow_model, scores_a, as_of_a
) -> None:
    cal = ConformalCalibrator(SplitConformal()).fit(scores_a)
    raw = narrow_model.predict(known_bars.iloc[SPLIT_AT - 39 : SPLIT_AT + 1], 2, 80, as_of_a)
    assert cal.calibrate(raw).horizon == 2


def test_the_convenience_function_matches_the_class(
    known_bars, narrow_model, scores_a, as_of_a
) -> None:
    raw = narrow_model.predict(
        known_bars.iloc[SPLIT_AT - 39 : SPLIT_AT + 1], H, 80, as_of_a, seed=2
    )
    via_fn = calibrate(raw, scores_a)
    via_cls = ConformalCalibrator(SplitConformal()).fit(scores_a).calibrate(raw)
    np.testing.assert_array_equal(via_fn.quantiles, via_cls.quantiles)
    assert (
        calibrate(raw, scores_a, method="adaptive", gamma=0.02).calibration.method
        == "adaptive-conformal"
    )


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: SplitConformal(window=10, min_scores=30), "window"),
        (lambda: SplitConformal(min_scores=1), "min_scores"),
        (lambda: AdaptiveConformal(gamma=0.0), "gamma"),
        (lambda: AdaptiveConformal(gamma=1.0), "gamma"),
        (lambda: AdaptiveConformal(window=5, min_scores=30), "window"),
    ],
)
def test_bad_calibrator_settings_are_rejected(factory, message) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


# ----------------------------------------------------------- non-stationary data
@pytest.fixture(scope="module")
def shifted(gaussian_cls) -> tuple[ScoreSet, pd.Timestamp]:
    """Volatility doubles halfway; the forecaster (sigma 0.012) was right only before that."""
    from tycheon.data.schema import normalize_bars

    rng = np.random.default_rng(11)
    r = np.concatenate([0.012 * rng.standard_normal(1200), 0.024 * rng.standard_normal(1300)])
    close = 100.0 * np.exp(np.cumsum(r))
    index = pd.bdate_range("2000-01-03", periods=len(close), tz="UTC")
    frame = pd.DataFrame(
        {"open": close, "high": close * 1.001, "low": close * 0.999, "close": close,
         "volume": 1e6, "amount": 1e6 * close},
        index=index,
    )  # fmt: skip
    bars = normalize_bars(frame, bar_duration=pd.Timedelta("1D"))
    s = collect_scores(
        gaussian_cls("calm", sigma=0.012), bars, as_of=bars["available_at"].iloc[-1], horizon=1,
        n_origins=2300, stride=1, max_history=5, n_samples=100,
    )  # fmt: skip
    return s, pd.Timestamp(bars["available_at"].iloc[1200 + 300])


def _online_coverage(scores: ScoreSet, method, since: pd.Timestamp) -> tuple[float, float]:
    rep = method.replay(scores)
    lo, hi = scores.levels.index(0.05), scores.levels.index(0.95)
    rows = np.flatnonzero(scores.origin_times >= since)
    d = rep.delta_used[rows][:, [lo, hi], 0]
    ok = np.isfinite(d).all(axis=1)
    y = scores.realized[rows][ok, 0]
    low = scores.base_logq[rows][ok, lo, 0] + d[ok, 0]
    high = scores.base_logq[rows][ok, hi, 0] + d[ok, 1]
    raw = (y >= scores.base_logq[rows][ok, lo, 0]) & (y <= scores.base_logq[rows][ok, hi, 0])
    return float(((y >= low) & (y <= high)).mean()), float(raw.mean())


def test_adaptive_conformal_restores_coverage_after_a_volatility_shift(shifted) -> None:
    """Measured after volatility doubles: raw 0.58, pooled 0.78, windowed 0.90, ACI 0.90."""
    shifted_scores, since = shifted
    aci, raw = _online_coverage(shifted_scores, AdaptiveConformal(gamma=0.03), since)
    split, _ = _online_coverage(shifted_scores, SplitConformal(), since)
    assert raw < 0.65, "the shift must really break the raw forecast"
    assert abs(aci - 0.9) < 0.03, f"ACI coverage {aci:.3f}"
    assert split < 0.84, f"pooled split should lag the shift, got {split:.3f}"
    assert aci > split + 0.05


def test_a_window_is_the_simple_fix_for_slow_drift_in_split_conformal(shifted) -> None:
    shifted_scores, since = shifted
    windowed, _ = _online_coverage(shifted_scores, SplitConformal(window=200), since)
    assert abs(windowed - 0.9) < 0.04


def test_status_goes_stale_when_recent_coverage_breaks(shifted) -> None:
    """Fit a pooled split calibrator right after the shift: its recent coverage is off-target."""
    shifted_scores, since = shifted
    keep = np.flatnonzero(shifted_scores.origin_times <= since + pd.Timedelta(days=200))
    cal = ConformalCalibrator(SplitConformal(), holdout_fraction=0.15).fit(
        shifted_scores.take(keep)
    )
    status, achieved, tol = cal.status()
    assert status == "stale"
    assert abs(achieved - 0.9) > tol


def test_adaptive_calibrator_reports_its_effective_levels(shifted) -> None:
    shifted_scores, _ = shifted
    rep = AdaptiveConformal(gamma=0.03).replay(shifted_scores)
    tau = rep.state["tau_effective"]
    assert tau.shape == (len(shifted_scores.levels), 1)
    # volatility doubled, so the upper effective levels were pushed up and lower ones down
    j_hi, j_lo = shifted_scores.levels.index(0.95), shifted_scores.levels.index(0.05)
    assert tau[j_hi, 0] > 0.95 and tau[j_lo, 0] < 0.05
    assert 0.0 <= rep.state["degenerate_fraction"] <= 1.0
