"""CalibratedForecaster: an inner forecaster plus a fitted calibrator, behind one interface."""

from __future__ import annotations

import pytest
from tests.conftest import TRUE_SIGMA

from tycheon.calibration import (
    CalibratedForecaster,
    ConformalCalibrator,
    SplitConformal,
    collect_scores,
)
from tycheon.errors import LookaheadError, ModelError

H = 5


@pytest.fixture(scope="module")
def narrow_model(gaussian_cls):
    return gaussian_cls("narrow", TRUE_SIGMA / 2)


@pytest.fixture(scope="module")
def fitted(known_bars, narrow_model) -> CalibratedForecaster:
    history = known_bars.iloc[:3000]
    return CalibratedForecaster.fit_on(
        narrow_model,
        history,
        as_of=history["available_at"].iloc[-1],
        horizon=H,
        n_origins=200,
        n_samples=60,
        max_history=60,
    )


def test_it_satisfies_the_forecaster_surface(fitted, narrow_model) -> None:
    assert fitted.model_id == "narrow"
    assert fitted.model_card == narrow_model.model_card
    assert fitted.supports_paths is True
    assert fitted.fit() is fitted


def test_it_widens_an_overconfident_forecast_and_records_the_evidence(
    fitted, narrow_model, known_bars
) -> None:
    history = known_bars.iloc[:3000]
    as_of = history["available_at"].iloc[-1]
    raw = narrow_model.predict(history, H, 200, as_of, seed=3)
    out = fitted.predict(history, H, 200, as_of, seed=3)
    raw_lo, raw_hi = raw.interval(0.9)
    lo, hi = out.interval(0.9)
    assert (hi - lo)[-1] > 1.5 * (raw_hi - raw_lo)[-1]
    assert out.calibration_status in ("calibrated", "stale")
    assert out.calibration is not None and out.calibration.holdout_n > 0
    assert out.calibration.raw_holdout_coverage[0.9] < 0.7  # the raw model really was overconfident
    assert abs(out.calibration.holdout_coverage[0.9] - 0.9) < 0.1


def test_the_model_id_can_be_renamed(known_bars, narrow_model) -> None:
    history = known_bars.iloc[:1500]
    model = CalibratedForecaster.fit_on(
        narrow_model, history, as_of=history["available_at"].iloc[-1], horizon=H,
        n_origins=60, n_samples=30, max_history=60, model_id="mine",
    )  # fmt: skip
    assert model.model_id == "mine"


def test_a_calibrator_that_was_never_fitted_is_refused(narrow_model) -> None:
    with pytest.raises(ValueError, match="fit the calibrator"):
        CalibratedForecaster(narrow_model, ConformalCalibrator(SplitConformal()))


def test_from_scores_matches_fit_on(known_bars, narrow_model) -> None:
    history = known_bars.iloc[:1500]
    as_of = history["available_at"].iloc[-1]
    scores = collect_scores(
        narrow_model, history, as_of=as_of, horizon=H, n_origins=60, n_samples=30, max_history=60
    )
    a = CalibratedForecaster.from_scores(narrow_model, scores, method=SplitConformal())
    b = CalibratedForecaster.fit_on(
        narrow_model, history, as_of=as_of, horizon=H, n_origins=60, n_samples=30,
        max_history=60, method=SplitConformal(),
    )  # fmt: skip
    qa = a.predict(history, H, 50, as_of, seed=1).quantiles
    qb = b.predict(history, H, 50, as_of, seed=1).quantiles
    assert qa == pytest.approx(qb)


def test_calibration_data_from_after_the_forecast_is_refused(known_bars, narrow_model) -> None:
    history = known_bars.iloc[:1500]
    model = CalibratedForecaster.fit_on(
        narrow_model, history, as_of=history["available_at"].iloc[-1], horizon=H,
        n_origins=60, n_samples=30, max_history=60,
    )  # fmt: skip
    earlier = history.iloc[:1000]
    with pytest.raises(LookaheadError):
        model.predict(earlier, H, 30, earlier["available_at"].iloc[-1])


def test_too_little_history_returns_the_raw_forecast_marked_uncalibrated(
    known_bars, narrow_model
) -> None:
    history = known_bars.iloc[:300]
    as_of = history["available_at"].iloc[-1]
    model = CalibratedForecaster.fit_on(
        narrow_model, history, as_of=as_of, horizon=H, n_origins=8, n_samples=30, max_history=60
    )
    out = model.predict(history, H, 30, as_of)
    assert out.calibration_status == "uncalibrated"
    assert "calibration_unavailable" in out.metadata.diagnostics


def test_an_unsupported_horizon_is_an_error(fitted, known_bars) -> None:
    history = known_bars.iloc[:3000]
    with pytest.raises(ModelError, match="exceeds the calibrated horizon"):
        fitted.predict(history, H + 3, 30, history["available_at"].iloc[-1])
