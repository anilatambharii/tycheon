"""The contract every forecaster must satisfy, run against every implementation.

Baselines run for real. Kronos runs through the real sampler on a tiny randomly
initialised model; TimesFM and Chronos run against fakes with the same call contract
as the real engines (the real ones are exercised by the slow tests). What is checked
here is Tycheon behaviour, not model quality: shapes, dtypes, provenance, determinism
and, above all, that none of them can see past ``as_of``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tycheon.data import AsOfStore
from tycheon.data.sample import SampleProvider
from tycheon.errors import DataValidationError, LookaheadError
from tycheon.models.base import DISCLAIMER, ForecastDistribution, Forecaster
from tycheon.models.baselines import (
    ARIMAForecaster,
    DriftForecaster,
    GARCHForecaster,
    RandomWalkForecaster,
    SeasonalNaiveForecaster,
)
from tycheon.models.chronos import ChronosForecaster
from tycheon.models.kronos import KronosForecaster
from tycheon.models.timesfm import TimesFMForecaster

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CARD_SECTIONS = ("## Source", "## License", "## Training data", "## Limitations", "## Calibration")

IDS = [
    "random-walk",
    "drift",
    "seasonal-naive",
    "arima",
    "garch",
    "kronos",
    "timesfm",
    "chronos",
]


@pytest.fixture(params=IDS)
def forecaster(request):
    kind = request.param
    if kind == "random-walk":
        return RandomWalkForecaster()
    if kind == "drift":
        return DriftForecaster()
    if kind == "seasonal-naive":
        return SeasonalNaiveForecaster(season_length=5)
    if kind == "arima":
        return ARIMAForecaster()
    if kind == "garch":
        return GARCHForecaster()
    if kind == "kronos":
        tokenizer, model, max_context = request.getfixturevalue("tiny_kronos")
        return KronosForecaster(
            "small", tokenizer=tokenizer, model=model, max_context=max_context, device="cpu"
        )
    if kind == "timesfm":
        return TimesFMForecaster(engine=request.getfixturevalue("fake_timesfm"))
    return ChronosForecaster(pipeline=request.getfixturevalue("fake_chronos"))


def _predict(model, bars, n=300, horizon=5, n_samples=24, **kwargs) -> ForecastDistribution:
    history = bars.iloc[:n]
    return model.predict(history, horizon, n_samples, history["available_at"].iloc[-1], **kwargs)


# ------------------------------------------------------------------ the protocol
def test_it_satisfies_the_forecaster_protocol(forecaster) -> None:
    assert isinstance(forecaster, Forecaster)
    assert forecaster.model_id
    assert isinstance(forecaster.supports_paths, bool)


def test_fit_is_optional_and_returns_the_model(forecaster) -> None:
    assert forecaster.fit() is forecaster


# --------------------------------------------------------- shapes, dtypes, values
def test_output_shapes_and_dtypes(forecaster, syn_gbm) -> None:
    d = _predict(forecaster, syn_gbm, horizon=7, n_samples=24)
    assert isinstance(d, ForecastDistribution)
    assert d.horizon == 7 == len(d.index)
    assert d.quantiles.dtype == np.float64
    assert d.quantiles.shape == (len(d.quantile_levels), 7)
    assert np.isfinite(d.quantiles).all()
    assert (np.diff(d.quantiles, axis=0) >= 0).all(), "quantiles must not cross"
    assert d.median.shape == (7,)
    if forecaster.supports_paths:
        assert d.samples is not None
        assert d.samples.dtype == np.float64
        assert d.samples.shape == (24, 7)
        assert np.isfinite(d.samples).all()
    else:
        assert d.samples is None
    assert d.has_paths == forecaster.supports_paths


def test_forecast_times_continue_the_history(forecaster, syn_gbm) -> None:
    d = _predict(forecaster, syn_gbm, n=300)
    last = syn_gbm.index[299]
    assert d.index.tz is not None
    assert d.index[0] > last
    assert d.last_observation == last
    assert d.index.is_monotonic_increasing
    assert (d.index.dayofweek < 5).all(), "business-day history must forecast business days"


def test_the_median_is_a_sensible_price(forecaster, syn_gbm) -> None:
    """Not skill: just that nothing is off by an order of magnitude or non-positive."""
    d = _predict(forecaster, syn_gbm)
    assert (d.median > 0).all()
    assert (d.median > d.last_close / 3).all()
    assert (d.median < d.last_close * 3).all()


def test_a_single_step_horizon_works(forecaster, syn_gbm) -> None:
    assert _predict(forecaster, syn_gbm, horizon=1, n_samples=8).horizon == 1


# --------------------------------------------------------- what AGENTS.md demands
def test_every_forecast_carries_uncertainty_calibration_mix_as_of_and_card(
    forecaster, syn_gbm
) -> None:
    n = 300
    d = _predict(forecaster, syn_gbm, n=n)
    assert d.quantile_levels, "uncertainty is not optional"
    assert d.calibration_status == "uncalibrated", "no model is calibrated before T2"
    assert d.model_mix == {forecaster.model_id: 1.0}
    assert d.as_of == pd.Timestamp(syn_gbm["available_at"].iloc[n - 1])
    assert d.model_card == forecaster.model_card
    assert d.disclaimer == DISCLAIMER
    assert d.summary().endswith(DISCLAIMER)
    record = d.to_dict()
    json.dumps(record)
    for key in (
        "quantiles",
        "calibration_status",
        "model_mix",
        "as_of",
        "model_card",
        "disclaimer",
    ):
        assert key in record


def test_metadata_records_provenance(forecaster, syn_gbm) -> None:
    d = _predict(forecaster, syn_gbm, n=300, horizon=4, n_samples=16)
    meta = d.metadata
    assert meta.model_id == forecaster.model_id
    assert meta.model_version
    assert meta.horizon == 4
    assert meta.context_length_available == 300
    assert 1 <= meta.context_length_used <= 300
    assert meta.device
    assert meta.n_samples == (16 if forecaster.supports_paths else 0)


def test_the_model_card_exists_and_says_what_it_must(forecaster) -> None:
    """No card, no routing: every forecaster ships documentation with the required sections."""
    card = PROJECT_ROOT / forecaster.model_card
    assert card.is_file(), f"missing model card {forecaster.model_card}"
    text = card.read_text(encoding="utf-8")
    for section in CARD_SECTIONS:
        assert section in text, f"{forecaster.model_card} lacks {section!r}"
    assert "Not investment advice" in text, "the disclaimer belongs on model cards too"


# ------------------------------------------------------------------ determinism
def test_the_same_seed_gives_the_same_forecast(forecaster, syn_gbm) -> None:
    a = _predict(forecaster, syn_gbm, seed=11)
    b = _predict(forecaster, syn_gbm, seed=11)
    np.testing.assert_array_equal(a.quantiles, b.quantiles)
    if a.samples is not None:
        np.testing.assert_array_equal(a.samples, b.samples)


def test_different_seeds_differ_when_the_model_samples(forecaster, syn_gbm) -> None:
    a = _predict(forecaster, syn_gbm, seed=1)
    b = _predict(forecaster, syn_gbm, seed=2)
    if forecaster.supports_paths:
        assert not np.array_equal(a.samples, b.samples)
    else:
        np.testing.assert_array_equal(a.quantiles, b.quantiles)  # deterministic models


def test_fitting_first_does_not_change_the_forecast(forecaster, syn_gbm) -> None:
    before = _predict(forecaster, syn_gbm, seed=3)
    forecaster.fit(syn_gbm.iloc[:300])
    after = _predict(forecaster, syn_gbm, seed=3)
    np.testing.assert_array_equal(before.quantiles, after.quantiles)


# ------------------------------------------------------------------ point-in-time
@pytest.mark.leakage
def test_a_history_published_after_as_of_is_refused(forecaster, syn_gbm) -> None:
    history = syn_gbm.iloc[:300]
    too_early = pd.Timestamp(history["available_at"].iloc[250])
    with pytest.raises(LookaheadError):
        forecaster.predict(history, 5, 8, too_early)


@pytest.mark.leakage
def test_a_naive_as_of_is_refused(forecaster, syn_gbm) -> None:
    with pytest.raises(DataValidationError, match="timezone-aware"):
        forecaster.predict(syn_gbm.iloc[:300], 5, 8, pd.Timestamp("2030-01-01"))


@pytest.mark.leakage
def test_bars_without_availability_must_not_be_dated_after_as_of(forecaster, syn_gbm) -> None:
    bare = syn_gbm.iloc[:300].drop(columns="available_at")
    with pytest.raises(LookaheadError):
        forecaster.predict(bare, 5, 8, bare.index[200])


@pytest.mark.leakage
def test_bars_published_later_never_change_a_forecast_made_earlier(forecaster, tmp_path) -> None:
    """The end-to-end leakage test: store, as-of read, forecast.

    Ingest the first stretch of a series, forecast from the store at ``as_of``, then
    ingest everything that came *after* (including a restatement of a past bar) and
    forecast again at the same ``as_of``. The forecast must be identical.
    """
    store = AsOfStore(tmp_path)
    as_of = pd.Timestamp("2019-12-31", tz="UTC")
    provider = SampleProvider()
    store.ingest(provider, "SYN-GBM", start=None, end=None, as_of=as_of)

    first = store.bars("SYN-GBM", as_of=as_of)
    before = forecaster.predict(first, 5, 12, as_of, seed=5)

    # Later knowledge arrives: the rest of the series, plus a restated bar from the past.
    store.ingest(
        provider, "SYN-GBM", start=None, end=None, as_of=pd.Timestamp("2023-12-31", tz="UTC")
    )
    restated = first.iloc[[-3]].copy()
    restated["close"] = restated["close"] * 3
    restated["high"] = restated["high"] * 3
    restated["available_at"] = pd.Timestamp("2021-01-01", tz="UTC")
    store.put_bars("SYN-GBM", restated)

    second = store.bars("SYN-GBM", as_of=as_of)
    after = forecaster.predict(second, 5, 12, as_of, seed=5)

    pd.testing.assert_frame_equal(first, second)
    np.testing.assert_array_equal(before.quantiles, after.quantiles)
    assert before.index.equals(after.index)
