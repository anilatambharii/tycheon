"""The point-in-time primitives. Everything else in the data layer rests on these."""

from __future__ import annotations

import pandas as pd
import pytest

from tycheon.data.asof import (
    as_utc,
    assert_available,
    filter_as_of,
    require_not_future,
)
from tycheon.errors import DataValidationError, LookaheadError


def test_as_utc_refuses_a_naive_timestamp() -> None:
    """A naive as_of is ambiguous by up to a day across timezones."""
    with pytest.raises(DataValidationError, match="timezone-aware"):
        as_utc(pd.Timestamp("2024-01-02"))


def test_as_utc_converts_to_utc() -> None:
    assert as_utc("2024-01-02T09:00:00-05:00") == pd.Timestamp("2024-01-02T14:00:00", tz="UTC")


@pytest.mark.leakage
def test_a_window_reaching_past_as_of_raises_rather_than_truncating() -> None:
    as_of = pd.Timestamp("2024-01-10", tz="UTC")
    require_not_future(pd.Timestamp("2024-01-10", tz="UTC"), as_of)  # equal is fine
    require_not_future(None, as_of)
    with pytest.raises(LookaheadError, match="after as_of"):
        require_not_future(pd.Timestamp("2024-01-11", tz="UTC"), as_of)


@pytest.mark.leakage
def test_assert_available_rejects_rows_known_only_after_as_of(bars_factory) -> None:
    bars = bars_factory(5)
    as_of = pd.Timestamp(bars["available_at"].iloc[2])
    assert_available(bars.iloc[:3], as_of)  # exactly at as_of is knowable
    with pytest.raises(LookaheadError):
        assert_available(bars, as_of)


def test_assert_available_needs_the_column(bars_factory) -> None:
    with pytest.raises(DataValidationError, match="available_at"):
        assert_available(
            bars_factory(3).drop(columns="available_at"), pd.Timestamp("2025-01-01", tz="UTC")
        )


def test_assert_available_accepts_an_empty_frame(bars_factory) -> None:
    assert_available(bars_factory(3).iloc[0:0], pd.Timestamp("2000-01-01", tz="UTC"))


@pytest.mark.leakage
def test_filter_as_of_keeps_the_version_that_was_current(bars_factory) -> None:
    """A restated bar must read as its original value until the restatement was published."""
    original = bars_factory(3)
    revised = original.iloc[[1]].copy()
    revised["close"] = 999.0
    revised["available_at"] = pd.Timestamp("2024-06-01", tz="UTC")
    combined = pd.concat([original, revised]).sort_index(kind="stable")

    before = filter_as_of(combined, pd.Timestamp("2024-05-01", tz="UTC"))
    after = filter_as_of(combined, pd.Timestamp("2024-07-01", tz="UTC"))

    assert before["close"].iloc[1] == original["close"].iloc[1]
    assert after["close"].iloc[1] == 999.0
    assert len(before) == len(after) == 3


@pytest.mark.leakage
def test_filter_as_of_drops_bars_not_yet_known(bars_factory) -> None:
    bars = bars_factory(6)
    cut = pd.Timestamp(bars["available_at"].iloc[2])
    assert list(filter_as_of(bars, cut).index) == list(bars.index[:3])
