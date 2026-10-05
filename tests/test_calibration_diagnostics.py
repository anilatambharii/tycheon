"""The calibration report: coverage, PIT, reliability and CRPS, raw versus calibrated."""

from __future__ import annotations

import json

import numpy as np
import pytest

from tycheon.calibration import (
    ConformalCalibrator,
    ScoreSet,
    SplitConformal,
    collect_scores,
    evaluate_calibration,
)
from tycheon.models.timesfm import TimesFMForecaster

H = 5
SIGMA = 0.012


@pytest.fixture(scope="module")
def scores(known_bars, gaussian_cls) -> ScoreSet:
    model = gaussian_cls("narrow-biased", sigma=0.5 * SIGMA, drift=0.002)
    return collect_scores(
        model, known_bars, as_of=known_bars["available_at"].iloc[-1], horizon=H, n_origins=500,
        max_history=40, n_samples=80,
    )  # fmt: skip


@pytest.fixture(scope="module")
def report(scores):
    return ConformalCalibrator(SplitConformal(), holdout_fraction=0.4).fit(scores).report


def test_calibrated_coverage_is_closer_to_nominal_than_raw_at_every_level(report) -> None:
    assert len(report.coverages) == 4
    for c in report.coverages:
        assert abs(c.calibrated - c.nominal) < abs(c.raw - c.nominal), c.nominal
        assert abs(c.calibrated - c.nominal) < 0.07, c.nominal


def test_coverage_rises_with_the_nominal_level(report) -> None:
    raw = [c.raw for c in report.coverages]
    cal = [c.calibrated for c in report.coverages]
    assert raw == sorted(raw) and cal == sorted(cal)
    assert [c.nominal for c in report.coverages] == [0.5, 0.8, 0.9, 0.95]


def test_the_too_narrow_forecast_widens_and_the_per_step_coverage_is_reported(report) -> None:
    c = report.coverage_at(0.9)
    assert c.width_calibrated > 1.5 * c.width_raw
    assert c.raw_by_step.shape == c.calibrated_by_step.shape == (H,)
    assert c.n_origins == report.n_holdout
    assert report.coverage_at(0.77) is None


def test_the_pit_histogram_is_flat_after_calibration_and_skewed_before(report) -> None:
    n_raw, n_cal = report.pit_raw.sum(), report.pit_calibrated.sum()
    assert n_raw > 0 and n_cal > 0
    mean = n_cal / report.pit_bins
    assert report.pit_calibrated.max() < 1.7 * mean
    assert report.pit_calibrated.min() > 0.4 * mean
    # biased high and too narrow: the raw PIT piles up at the low end and in the tails
    assert report.pit_raw.max() > 2.0 * (n_raw / report.pit_bins)


def test_the_reliability_curve_sits_on_the_diagonal_after_calibration(report) -> None:
    levels = np.asarray(report.reliability_levels)
    cal = report.reliability_calibrated
    raw = report.reliability_raw
    central = (levels >= 0.1) & (levels <= 0.9)
    assert np.abs(cal[central] - levels[central]).max() < 0.08
    assert np.abs(raw[central] - levels[central]).max() > 0.15


def test_crps_and_the_quantile_score_improve(report) -> None:
    assert report.crps_calibrated < report.crps_raw
    assert report.quantile_score_calibrated < report.quantile_score_raw


def test_the_report_is_json_serialisable_and_complete(report) -> None:
    record = report.to_dict()
    json.dumps(record)
    for key in (
        "method", "coverages", "pit_raw", "pit_calibrated", "reliability_levels",
        "reliability_raw", "reliability_calibrated", "crps_raw", "quantile_score_raw",
    ):  # fmt: skip
        assert key in record
    assert record["method"] == "split-conformal" and record["model_id"] == "narrow-biased"


def test_a_quantile_only_model_has_no_crps_but_still_a_report(known_bars, fake_timesfm) -> None:
    s = collect_scores(
        TimesFMForecaster(engine=fake_timesfm), known_bars,
        as_of=known_bars["available_at"].iloc[-1], horizon=H, n_origins=200, max_history=60,
    )  # fmt: skip
    rep = ConformalCalibrator(SplitConformal(), holdout_fraction=0.4).fit(s).report
    assert rep.crps_raw is None and rep.crps_calibrated is None
    assert rep.coverages, "coverage is still reported on the levels the model has"
    assert all(c.nominal <= 0.8 + 1e-9 for c in rep.coverages), (
        "no 90% interval from 0.1-0.9 quantiles"
    )


def test_with_no_usable_holdout_the_report_says_so(scores) -> None:
    nan = np.full((scores.n, len(scores.levels), H), np.nan)
    rep = evaluate_calibration(scores, nan, holdout=20, method="none")
    assert rep.n_holdout == 0 and rep.coverages == ()
    assert rep.pit_calibrated.sum() == 0
    assert np.isnan(rep.quantile_score_calibrated)


def test_a_level_the_data_cannot_support_does_not_block_the_report(
    known_bars, gaussian_cls
) -> None:
    """Regression: a 1% tail with 60 scores used to blank the whole holdout evaluation."""
    s = collect_scores(
        gaussian_cls("g", 0.5 * SIGMA), known_bars, as_of=known_bars["available_at"].iloc[-1],
        horizon=H, n_origins=60, max_history=40, n_samples=80,
    )  # fmt: skip
    cal = ConformalCalibrator(SplitConformal(min_scores=20), min_holdout=5).fit(s)
    assert cal.report.n_holdout >= 5
    assert cal.report.coverage_at(0.9) is not None
    assert 0.01 not in cal.adjustment.levels
