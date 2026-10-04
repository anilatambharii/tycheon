"""The bars schema: canonical form, validation, and the symbol allow-list."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tycheon.data.schema import (
    infer_bar_duration,
    normalize_bars,
    validate_bars,
    validate_symbol,
)
from tycheon.errors import DataValidationError


def _raw(n: int = 4) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="1D")  # naive on purpose
    close = np.arange(n, dtype=float) + 10
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 5},
        index=index,
    )


def test_normalize_localises_naive_timestamps_and_converts_to_utc() -> None:
    out = normalize_bars(_raw(), tz="America/New_York", bar_duration=pd.Timedelta("1D"))
    assert str(out.index.tz) == "UTC"
    assert out.index[0] == pd.Timestamp("2024-01-01 05:00", tz="UTC")
    assert out.index.name == "timestamp"


def test_availability_defaults_to_the_conservative_bar_close() -> None:
    """A daily bar is not knowable before the day ends; never assume it is."""
    out = normalize_bars(
        _raw(), bar_duration=pd.Timedelta("1D"), availability_lag=pd.Timedelta("1h")
    )
    assert (out["available_at"] - out.index == pd.Timedelta("25h")).all()


def test_normalize_refuses_to_guess_availability() -> None:
    with pytest.raises(DataValidationError, match="bar_duration"):
        normalize_bars(_raw())


def test_normalize_keeps_a_supplied_available_at(bars_factory) -> None:
    bars = bars_factory(3)
    out = normalize_bars(bars.drop(columns="amount"))
    assert (out["available_at"] == bars["available_at"]).all()
    assert "amount" not in out.columns


def test_normalize_casts_to_float_and_orders_columns() -> None:
    raw = _raw()
    raw["close"] = raw["close"].astype(int)
    out = normalize_bars(raw, bar_duration=pd.Timedelta("1D"))
    assert out["close"].dtype == np.float64
    assert list(out.columns)[:4] == ["open", "high", "low", "close"]


def test_a_well_formed_frame_validates(bars_factory) -> None:
    validate_bars(bars_factory(5))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda f: f.set_axis(f.index.tz_localize(None)), "timezone-aware"),
        (lambda f: f.iloc[::-1], "sorted"),
        (lambda f: f.drop(columns="close"), "missing required columns"),
        (lambda f: f.drop(columns="available_at"), "available_at"),
        (lambda f: f.assign(close=np.where(np.arange(len(f)) == 1, np.nan, f["close"])), "NaN"),
        (lambda f: f.assign(open=-1.0), "non-positive"),
        (lambda f: f.assign(high=f["low"] - 1), "high < low"),
        (lambda f: f.assign(available_at=f.index - pd.Timedelta("1D")), "future"),
    ],
    ids=[
        "naive",
        "unsorted",
        "no-close",
        "no-available_at",
        "nan",
        "negative",
        "high<low",
        "early",
    ],
)
def test_validation_rejects_malformed_bars(bars_factory, mutate, message) -> None:
    with pytest.raises(DataValidationError, match=message):
        validate_bars(mutate(bars_factory(5)))


def test_validation_rejects_a_non_datetime_index() -> None:
    frame = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]})
    with pytest.raises(DataValidationError, match="DatetimeIndex"):
        validate_bars(frame)


def test_duplicate_timestamps_are_only_valid_as_revisions(bars_factory) -> None:
    bars = bars_factory(3)
    duplicated = pd.concat([bars, bars.iloc[[1]]]).sort_index(kind="stable")
    with pytest.raises(DataValidationError, match="duplicate"):
        validate_bars(duplicated)
    validate_bars(duplicated, allow_revisions=True)


def test_infer_bar_duration(bars_factory) -> None:
    assert infer_bar_duration(bars_factory(10).index) == pd.Timedelta("1D")
    with pytest.raises(DataValidationError, match="at least two"):
        infer_bar_duration(bars_factory(1).index)


@pytest.mark.parametrize(
    "symbol", ["AAPL", "BRK.B", "BTC-USD", "EURUSD=X", "^GSPC", "SYN-GBM-H", "a1"]
)
def test_ordinary_symbols_are_accepted(symbol) -> None:
    assert validate_symbol(symbol) == symbol


@pytest.mark.parametrize(
    "symbol",
    [
        "",
        "..",
        "../etc/passwd",
        "a/b",
        "a\\b",
        "C:\\x",
        "x" * 33,
        " AAPL",
        "A B",
        "a\x00b",
        ".hidden",
        "-x",
    ],
)
def test_symbols_that_could_escape_a_directory_are_refused(symbol) -> None:
    """Symbols become directory and file names, so they are allow-listed, not sanitised."""
    with pytest.raises(DataValidationError, match="invalid symbol"):
        validate_symbol(symbol)


def test_a_non_string_symbol_is_refused() -> None:
    with pytest.raises(DataValidationError, match="invalid symbol"):
        validate_symbol(123)  # type: ignore[arg-type]
