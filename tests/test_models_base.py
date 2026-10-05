"""The forecaster contract: distributions carry what AGENTS.md requires, histories are guarded."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from tycheon.errors import DataValidationError, LookaheadError, ModelError
from tycheon.models.base import (
    DISCLAIMER,
    BaseForecaster,
    ForecastDistribution,
    ForecastMetadata,
    PreparedHistory,
    RawForecast,
    forecast_index,
    prepare_history,
)

LEVELS = (0.1, 0.5, 0.9)
LAST = pd.Timestamp("2024-01-10", tz="UTC")


def _meta(horizon: int = 3, n_samples: int = 4) -> ForecastMetadata:
    return ForecastMetadata(
        model_id="unit",
        model_version="1",
        context_length_used=10,
        context_length_available=10,
        horizon=horizon,
        n_samples=n_samples,
        seed=0,
        device="cpu",
    )


def _dist(**overrides) -> ForecastDistribution:
    horizon = overrides.pop("horizon", 3)
    kwargs = {
        "index": pd.date_range(LAST + pd.Timedelta("1D"), periods=horizon, freq="1D"),
        "quantile_levels": LEVELS,
        "quantiles": np.array([[9.0] * horizon, [10.0] * horizon, [11.0] * horizon]),
        "samples": np.linspace(9, 11, 4 * horizon).reshape(4, horizon),
        "last_close": 10.0,
        "as_of": LAST,
        "last_observation": LAST,
        "metadata": _meta(horizon),
        "model_mix": {"unit": 1.0},
        "model_card": "docs/models/unit.md",
    }
    kwargs.update(overrides)
    return ForecastDistribution(**kwargs)


# ------------------------------------------------------------ the distribution
def test_a_valid_distribution_exposes_the_basics() -> None:
    d = _dist()
    assert d.horizon == 3
    assert d.has_paths
    assert d.calibration_status == "uncalibrated"
    assert d.disclaimer == DISCLAIMER
    assert d.median.tolist() == [10.0, 10.0, 10.0]
    lo, hi = d.interval(0.8)
    assert (lo == 9.0).all() and (hi == 11.0).all()


def test_arrays_are_read_only_copies() -> None:
    source = np.array([[9.0, 9.0, 9.0], [10.0, 10.0, 10.0], [11.0, 11.0, 11.0]])
    d = _dist(quantiles=source)
    with pytest.raises(ValueError, match="read-only"):
        d.quantiles[0, 0] = 0.0
    with pytest.raises(ValueError, match="read-only"):
        d.samples[0, 0] = 0.0  # type: ignore[index]
    source[0, 0] = -1.0
    assert d.quantiles[0, 0] == 9.0  # not aliased to the caller array


def test_quantile_is_exact_computed_or_interpolated() -> None:
    d = _dist()
    assert (d.quantile(0.5) == 10.0).all()  # stored level
    samples = np.arange(40, dtype=float).reshape(4, 10)
    from_paths = _dist(
        horizon=10, samples=samples, quantiles=np.sort(np.random.default_rng(0).random((3, 10)), 0)
    )
    assert from_paths.quantile(0.25) == pytest.approx(np.quantile(samples, 0.25, axis=0))

    only_quantiles = _dist(samples=None)
    assert only_quantiles.quantile(0.3) == pytest.approx([9.5, 9.5, 9.5])  # between 0.1 and 0.5
    with pytest.raises(ValueError, match="outside"):
        only_quantiles.quantile(0.01)  # never extrapolated
    with pytest.raises(ValueError, match="strictly between"):
        only_quantiles.quantile(1.0)
    with pytest.raises(ValueError, match="strictly between"):
        only_quantiles.interval(0.0)


def test_quantile_only_forecasts_refuse_path_dependent_questions() -> None:
    d = _dist(samples=None)
    assert not d.has_paths
    with pytest.raises(ModelError, match=r"joint sample paths.*unit"):
        d.require_paths("drawdown probability")
    assert _dist().require_paths("drawdown probability").shape == (4, 3)


@pytest.mark.parametrize(
    ("overrides", "error", "message"),
    [
        (
            {
                "horizon": 0,
                "index": pd.DatetimeIndex([], tz="UTC"),
                "quantiles": np.empty((3, 0)),
                "samples": None,
            },
            ModelError,
            "at least one step",
        ),
        (
            {"index": pd.date_range("2024-01-11", periods=3, freq="1D")},
            DataValidationError,
            "timezone-aware",
        ),
        ({"as_of": pd.Timestamp("2024-01-10")}, DataValidationError, "timezone-aware"),
        (
            {"index": pd.date_range("2024-01-09", periods=3, freq="1D", tz="UTC")},
            LookaheadError,
            "after the last observation",
        ),
        (
            {"index": pd.DatetimeIndex(["2024-01-12", "2024-01-11", "2024-01-13"], tz="UTC")},
            DataValidationError,
            "strictly increasing",
        ),
        ({"quantile_levels": (0.5, 0.1, 0.9)}, DataValidationError, "strictly increasing"),
        ({"quantile_levels": (0.0, 0.5, 0.9)}, DataValidationError, "within"),
        ({"quantile_levels": (0.1, 0.5, 1.0)}, DataValidationError, "within"),
        ({"quantiles": np.ones((2, 3))}, DataValidationError, "shape"),
        ({"quantiles": np.full((3, 3), np.nan)}, ModelError, "NaN"),
        (
            {"quantiles": np.array([[11.0] * 3, [10.0] * 3, [9.0] * 3])},
            DataValidationError,
            "cross",
        ),
        ({"samples": np.ones((4, 5))}, DataValidationError, "samples have shape"),
        ({"samples": np.full((4, 3), np.inf)}, ModelError, "NaN or infinity"),
        ({"model_card": "  "}, DataValidationError, "model card"),
        ({"model_mix": {"a": 0.5, "b": 0.4}}, DataValidationError, "sum to 1"),
        ({"model_mix": {}}, DataValidationError, "sum to 1"),
    ],
)
def test_invalid_distributions_are_rejected(overrides, error, message) -> None:
    with pytest.raises(error, match=message):
        _dist(**overrides)


def test_to_dict_keeps_everything_agents_md_requires_and_is_json_serialisable() -> None:
    record = _dist().to_dict()
    for required in (
        "quantiles",
        "quantile_levels",
        "calibration_status",
        "model_mix",
        "as_of",
        "model_card",
        "disclaimer",
    ):
        assert required in record, f"forecast record lost {required}"
    assert record["disclaimer"] == DISCLAIMER
    assert record["metadata"]["model_id"] == "unit"
    json.dumps(record)  # must not raise


def test_summary_always_carries_the_disclaimer() -> None:
    text = _dist().summary()
    assert text.endswith(DISCLAIMER)
    assert "uncalibrated" in text and "sample paths" in text
    assert "quantiles only" in _dist(samples=None).summary()


def test_a_quantile_only_forecast_reports_the_widest_interval_it_has() -> None:
    """TimesFM-style levels (0.1 to 0.9) give an 80% interval, not 90%; summary must not crash."""
    d = _dist(samples=None)
    assert d.max_coverage == pytest.approx(0.8)
    assert "80% interval" in d.summary()
    assert _dist().max_coverage == 0.9  # sample paths support any level
    lo, hi = d.interval(d.max_coverage)
    assert (lo <= hi).all()
    with pytest.raises(ValueError, match="outside"):
        d.interval(0.9)


def test_to_frame_is_indexed_by_forecast_time() -> None:
    frame = _dist().to_frame()
    assert list(frame.columns) == ["q0.1", "q0.5", "q0.9"]
    assert frame.index.equals(_dist().index)


# ------------------------------------------------------------ prepare_history
def _history(bars_factory, n: int = 30) -> pd.DataFrame:
    return bars_factory(n)


def test_a_valid_history_is_prepared(bars_factory) -> None:
    bars = _history(bars_factory)
    as_of = pd.Timestamp(bars["available_at"].iloc[-1])
    prepared = prepare_history(bars, 5, as_of)
    assert isinstance(prepared, PreparedHistory)
    assert prepared.last_close == bars["close"].iloc[-1]
    assert prepared.last_observation == bars.index[-1]
    assert prepared.future_index[0] > bars.index[-1]
    assert len(prepared.future_index) == 5
    assert "available_at" not in prepared.frame.columns


@pytest.mark.leakage
def test_a_history_known_only_after_as_of_is_refused(bars_factory) -> None:
    bars = _history(bars_factory)
    as_of = pd.Timestamp(bars["available_at"].iloc[-6])
    with pytest.raises(LookaheadError, match="available_at"):
        prepare_history(bars, 3, as_of)


@pytest.mark.leakage
def test_without_availability_the_bar_timestamps_must_not_pass_as_of(bars_factory) -> None:
    bars = _history(bars_factory).drop(columns="available_at")
    with pytest.raises(LookaheadError, match="after as_of"):
        prepare_history(bars, 3, bars.index[-6])
    prepare_history(bars, 3, bars.index[-1])  # equal is fine


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda f: f.set_axis(f.index.tz_localize(None)), "timezone-aware"),
        (lambda f: f.set_axis(range(len(f))), "timezone-aware"),
        (lambda f: f.iloc[::-1], "strictly increasing"),
        (lambda f: pd.concat([f, f.iloc[[0]]]).sort_index(kind="stable"), "strictly increasing"),
        (lambda f: f.drop(columns="close"), "missing columns"),
        (lambda f: f.iloc[:1], "need at least"),
        (lambda f: f.assign(close=np.where(np.arange(len(f)) == 3, np.nan, f["close"])), "NaN"),
        (lambda f: f.assign(close=-1.0), "non-positive"),
    ],
)
def test_malformed_histories_are_refused(bars_factory, mutate, message) -> None:
    bars = mutate(_history(bars_factory))
    with pytest.raises(DataValidationError, match=message):
        prepare_history(bars, 3, pd.Timestamp("2030-01-01", tz="UTC"))


def test_prepare_history_rejects_non_dataframes() -> None:
    with pytest.raises(DataValidationError, match="DataFrame"):
        prepare_history([1, 2, 3], 3, pd.Timestamp("2030-01-01", tz="UTC"))  # type: ignore[arg-type]


@pytest.mark.parametrize("horizon", [0, -1, 1.5, True, "3", None])
def test_horizon_must_be_a_positive_integer(bars_factory, horizon) -> None:
    with pytest.raises(ValueError, match="horizon"):
        prepare_history(_history(bars_factory), horizon, pd.Timestamp("2030-01-01", tz="UTC"))


def test_numpy_integers_are_accepted_as_horizons(bars_factory) -> None:
    prepared = prepare_history(
        _history(bars_factory), np.int64(4), pd.Timestamp("2030-01-01", tz="UTC")
    )
    assert len(prepared.future_index) == 4


def test_as_of_must_be_timezone_aware(bars_factory) -> None:
    with pytest.raises(DataValidationError, match="timezone-aware"):
        prepare_history(_history(bars_factory), 3, pd.Timestamp("2030-01-01"))


# -------------------------------------------------------------- forecast_index
def test_business_day_series_continue_over_weekends() -> None:
    index = pd.bdate_range("2024-01-01", periods=30, tz="UTC")
    future = forecast_index(index, 6)
    assert (future.dayofweek < 5).all()
    assert future[0] == index[-1] + pd.offsets.BDay()


def test_business_day_series_with_holidays_are_still_business_days() -> None:
    index = pd.bdate_range("2024-01-01", periods=40, tz="UTC").delete([5, 17])  # irregular
    future = forecast_index(index, 5)
    assert (future.dayofweek < 5).all()


def test_calendar_daily_and_hourly_cadences_are_continued() -> None:
    daily = pd.date_range("2024-01-01", periods=30, freq="1D", tz="UTC")
    assert forecast_index(daily, 3)[1] - forecast_index(daily, 3)[0] == pd.Timedelta("1D")
    hourly = pd.date_range("2024-01-01", periods=48, freq="1h", tz="UTC")
    assert forecast_index(hourly, 3)[0] == hourly[-1] + pd.Timedelta("1h")


def test_irregular_intraday_spacing_falls_back_to_the_median_step() -> None:
    index = pd.DatetimeIndex(
        pd.to_datetime(
            [
                "2024-01-01 09:00",
                "2024-01-01 09:05",
                "2024-01-01 09:10",
                "2024-01-01 09:20",
                "2024-01-01 09:25",
            ],
            utc=True,
        )
    )
    future = forecast_index(index, 2)
    assert future[0] == index[-1] + pd.Timedelta("5min")


def test_the_history_timezone_is_preserved() -> None:
    index = pd.date_range("2024-01-01 09:30", periods=20, freq="5min", tz="America/New_York")
    assert str(forecast_index(index, 3).tz) == "America/New_York"


# ------------------------------------------------------------ BaseForecaster
class _Fixed(BaseForecaster):
    model_id = "fixed"
    model_card = "docs/models/fixed.md"

    def __init__(self, raw: RawForecast, **kwargs) -> None:
        super().__init__(**kwargs)
        self.raw = raw

    def _forecast(self, prepared, horizon, n_samples, seed) -> RawForecast:
        self.seen = (horizon, n_samples, seed)
        return self.raw


def _predict(model, bars_factory, **kwargs):
    bars = bars_factory(30)
    as_of = pd.Timestamp(bars["available_at"].iloc[-1])
    return model.predict(
        bars, kwargs.pop("horizon", 3), kwargs.pop("n_samples", 4), as_of, **kwargs
    )


def test_samples_become_quantiles_and_provenance(bars_factory) -> None:
    samples = np.arange(12, dtype=float).reshape(4, 3) + 100
    model = _Fixed(
        RawForecast(samples=samples, model_version="v9", context_length_used=30, device="cpu")
    )
    d = _predict(model, bars_factory)
    assert d.metadata.model_id == "fixed"
    assert d.metadata.model_version == "v9"
    assert d.metadata.n_samples == 4
    assert d.metadata.context_length_available == 30
    assert d.model_mix == {"fixed": 1.0}
    assert d.model_card == "docs/models/fixed.md"
    assert d.quantile(0.5) == pytest.approx(np.quantile(samples, 0.5, axis=0))


def test_seed_override_is_recorded_and_passed_down(bars_factory) -> None:
    model = _Fixed(RawForecast(samples=np.full((2, 3), 100.0)), seed=7)
    assert _predict(model, bars_factory).metadata.seed == 7
    d = _predict(model, bars_factory, seed=99)
    assert d.metadata.seed == 99
    assert model.seen == (3, 4, 99)


def test_crossing_quantiles_are_repaired_and_flagged(bars_factory) -> None:
    crossing = np.array([[10.0, 10.0, 10.0], [9.0, 9.0, 9.0], [11.0, 11.0, 11.0]])
    model = _Fixed(RawForecast(samples=None, quantile_levels=LEVELS, quantiles=crossing))
    d = _predict(model, bars_factory)
    assert (np.diff(d.quantiles, axis=0) >= 0).all()
    assert d.metadata.diagnostics["quantile_crossing_repaired"] is True
    assert not d.has_paths


def test_non_crossing_quantiles_are_not_flagged(bars_factory) -> None:
    good = np.array([[9.0] * 3, [10.0] * 3, [11.0] * 3])
    d = _predict(
        _Fixed(RawForecast(samples=None, quantile_levels=LEVELS, quantiles=good)), bars_factory
    )
    assert "quantile_crossing_repaired" not in d.metadata.diagnostics


def test_a_model_returning_nothing_is_an_error(bars_factory) -> None:
    with pytest.raises(ModelError, match="neither samples nor quantiles"):
        _predict(_Fixed(RawForecast(samples=None)), bars_factory)


def test_paths_that_go_non_positive_are_counted_not_hidden(bars_factory) -> None:
    samples = np.array([[1.0, -1.0, 1.0], [2.0, 2.0, 2.0], [0.0, 1.0, 1.0], [3.0, 3.0, 3.0]])
    d = _predict(_Fixed(RawForecast(samples=samples)), bars_factory)
    assert d.metadata.diagnostics["paths_with_nonpositive_close"] == 2


@pytest.mark.parametrize("n_samples", [0, -3, 2.5, True])
def test_n_samples_must_be_a_positive_integer(bars_factory, n_samples) -> None:
    with pytest.raises(ValueError, match="n_samples"):
        _predict(_Fixed(RawForecast(samples=np.ones((2, 3)))), bars_factory, n_samples=n_samples)


def test_fit_is_a_no_op_that_returns_the_model() -> None:
    model = _Fixed(RawForecast(samples=np.ones((2, 3))))
    assert model.fit() is model
    assert model.fit(pd.DataFrame()) is model


@pytest.mark.leakage
def test_the_base_class_refuses_lookahead_before_any_model_code_runs(bars_factory) -> None:
    model = _Fixed(RawForecast(samples=np.ones((2, 3))))
    bars = bars_factory(30)
    with pytest.raises(LookaheadError):
        model.predict(bars, 3, 4, pd.Timestamp(bars["available_at"].iloc[10]))
    assert not hasattr(model, "seen"), "the model must not run when the history leaks"


# --------------------------------------------------- calibration evidence (T2)
def _info(scores_as_of=None, **overrides):
    from tycheon.models.base import CalibrationInfo

    kwargs = {
        "method": "split-conformal",
        "n_scores": 100,
        "scores_as_of": scores_as_of if scores_as_of is not None else LAST - pd.Timedelta("1D"),
        "holdout_n": 25,
        "holdout_coverage": {0.9: 0.91},
        "raw_holdout_coverage": {0.9: 0.6},
        "tolerance": 0.05,
        "calibrated_levels": (0.1, 0.5, 0.9),
    }
    kwargs.update(overrides)
    return CalibrationInfo(**kwargs)


def test_a_calibration_claim_needs_its_evidence() -> None:
    """'calibrated' without evidence is exactly the dishonest output the contract exists to stop."""
    with pytest.raises(DataValidationError, match="evidence"):
        _dist(calibration_status="calibrated")
    with pytest.raises(DataValidationError, match="evidence"):
        _dist(calibration_status="stale")
    _dist(calibration_status="calibrated", calibration=_info())
    _dist(calibration_status="stale", calibration=_info())
    _dist()  # uncalibrated needs nothing


@pytest.mark.leakage
def test_calibration_that_used_outcomes_after_as_of_is_refused() -> None:
    late = _info(scores_as_of=LAST + pd.Timedelta("1D"))
    with pytest.raises(LookaheadError, match="after this forecast's as_of"):
        _dist(calibration_status="calibrated", calibration=late)


def test_calibration_info_serialises_and_shows_in_the_summary() -> None:
    d = _dist(calibration_status="calibrated", calibration=_info())
    record = d.to_dict()
    assert record["calibration"]["holdout_coverage"] == {"0.9": 0.91}
    assert record["calibration"]["raw_holdout_coverage"] == {"0.9": 0.6}
    assert record["calibration"]["method"] == "split-conformal"
    json.dumps(record)
    assert "holdout 91% at nominal 90%" in d.summary()
    assert _dist().to_dict()["calibration"] is None
