"""Kronos input preparation. Pure numpy/pandas: no torch, no model."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tycheon.errors import DataValidationError
from tycheon.models.kronos.windowing import (
    CLOSE_INDEX,
    FEATURES,
    calendar_features,
    feature_matrix,
    window_context,
)


def _ohlc(n: int = 20) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    close = 100.0 + np.arange(n, dtype=float)
    return pd.DataFrame(
        {"open": close - 1, "high": close + 2, "low": close - 2, "close": close}, index=index
    )


# --------------------------------------------------------------- feature matrix
def test_the_channel_order_is_the_one_upstream_expects() -> None:
    assert FEATURES == ("open", "high", "low", "close", "volume", "amount")
    assert FEATURES[CLOSE_INDEX] == "close"


def test_missing_volume_and_amount_become_zeros_as_upstream_does() -> None:
    matrix = feature_matrix(_ohlc())
    assert matrix.shape == (20, 6)
    assert (matrix[:, 4:] == 0).all()


def test_volume_without_amount_gets_volume_times_mean_ohlc() -> None:
    frame = _ohlc(5).assign(volume=10.0)
    matrix = feature_matrix(frame)
    expected = 10.0 * frame[["open", "high", "low", "close"]].mean(axis=1).to_numpy()
    np.testing.assert_allclose(matrix[:, 5], expected)


def test_a_supplied_amount_is_used_as_is() -> None:
    frame = _ohlc(5).assign(volume=10.0, amount=7.0)
    assert (feature_matrix(frame)[:, 5] == 7.0).all()


def test_missing_prices_and_non_finite_values_are_rejected() -> None:
    with pytest.raises(DataValidationError, match="missing"):
        feature_matrix(_ohlc().drop(columns="high"))
    bad = _ohlc()
    bad.iloc[3, bad.columns.get_loc("close")] = np.nan
    with pytest.raises(DataValidationError, match="NaN"):
        feature_matrix(bad)


# ------------------------------------------------------------ calendar features
def test_calendar_features_are_minute_hour_weekday_day_month() -> None:
    index = pd.DatetimeIndex(["2024-03-05 14:35"], tz="UTC")  # a Tuesday
    features = calendar_features(index)
    assert features.dtype == np.float32
    assert features.tolist() == [[35.0, 14.0, 1.0, 5.0, 3.0]]


def test_calendar_features_use_the_index_own_timezone() -> None:
    """For intraday data this is the exchange clock Kronos was trained against."""
    utc = pd.DatetimeIndex(["2024-03-05 14:35"], tz="UTC")
    new_york = utc.tz_convert("America/New_York")
    assert calendar_features(utc)[0, 1] == 14.0
    assert calendar_features(new_york)[0, 1] == 9.0


# ------------------------------------------------------------------ the window
def test_a_short_history_is_used_whole() -> None:
    window = window_context(_ohlc(20), max_context=512)
    assert window.length == 20
    assert not window.truncated
    assert window.normalized.shape == (20, 6)
    assert window.stamps.shape == (20, 5)


def test_a_long_history_is_cut_to_the_most_recent_context() -> None:
    frame = _ohlc(100)
    window = window_context(frame, max_context=64)
    assert window.length == 64
    assert window.truncated
    np.testing.assert_allclose(window.mean[CLOSE_INDEX], frame["close"].iloc[-64:].mean())


def test_lookback_caps_the_window_but_never_beyond_max_context() -> None:
    frame = _ohlc(100)
    assert window_context(frame, max_context=64, lookback=30).length == 30
    assert window_context(frame, max_context=64, lookback=500).length == 64


def test_statistics_describe_the_window_the_model_sees_not_the_whole_history() -> None:
    """The deliberate difference from upstream.

    Upstream normalises over everything it is given, then lets the model read only the
    last ``max_context`` rows. Here the early, very different regime must not leak into
    the statistics that scale the part the model actually reads.
    """
    old_regime = _ohlc(200) * 0.01  # prices in a wildly different range
    old_regime.index = old_regime.index - pd.Timedelta(days=200)
    recent = _ohlc(64)
    frame = pd.concat([old_regime, recent])

    window = window_context(frame, max_context=64)
    whole = frame["close"].to_numpy()
    assert window.mean[CLOSE_INDEX] == pytest.approx(recent["close"].mean())
    assert window.mean[CLOSE_INDEX] != pytest.approx(whole.mean(), rel=0.1)

    close = window.normalized[:, CLOSE_INDEX]
    assert close.mean() == pytest.approx(0.0, abs=1e-5)
    assert close.std() == pytest.approx(1.0, abs=1e-3)


def test_normalised_values_are_clipped() -> None:
    frame = _ohlc(60)
    frame.iloc[30, frame.columns.get_loc("high")] = 1e9
    window = window_context(frame, max_context=64, clip=5.0)
    assert np.abs(window.normalized).max() <= 5.0


def test_denormalising_recovers_the_original_values() -> None:
    frame = _ohlc(40).assign(volume=np.linspace(100, 500, 40))
    window = window_context(frame, max_context=64, clip=1e9)
    recovered = window.denormalize(window.normalized)
    np.testing.assert_allclose(recovered, feature_matrix(frame), rtol=1e-4, atol=1e-3)


def test_constant_channels_do_not_produce_nan() -> None:
    """Volume and amount default to zero, whose standard deviation is zero."""
    window = window_context(_ohlc(30), max_context=64)
    assert np.isfinite(window.normalized).all()
    assert (window.normalized[:, 4:] == 0).all()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"max_context": 1}, "max_context"), ({"max_context": 64, "lookback": 1}, "lookback")],
)
def test_bad_window_parameters_are_rejected(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        window_context(_ohlc(30), **kwargs)


def test_one_bar_is_not_enough() -> None:
    with pytest.raises(DataValidationError, match="at least two"):
        window_context(_ohlc(1), max_context=64)
