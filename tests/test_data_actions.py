"""Corporate actions, and the point-in-time rule for adjusting history."""

from __future__ import annotations

import pandas as pd
import pytest

from tycheon.data.actions import (
    adjust_bars,
    cumulative_split_factor,
    empty_actions,
    normalize_actions,
)
from tycheon.errors import DataValidationError


def _split(ex_date: str, ratio: float = 2.0, announced: str | None = None) -> pd.DataFrame:
    frame = pd.DataFrame(
        {"kind": ["split"], "value": [ratio]}, index=pd.DatetimeIndex([ex_date], tz="UTC")
    )
    if announced is not None:
        frame["available_at"] = pd.Timestamp(announced, tz="UTC")
    return normalize_actions(frame)


def test_availability_defaults_to_the_ex_date() -> None:
    """Without an announcement time, the ex-date is the latest it could have been known."""
    actions = _split("2024-01-05")
    assert actions["available_at"].iloc[0] == pd.Timestamp("2024-01-05", tz="UTC")


@pytest.mark.parametrize(
    ("frame", "message"),
    [
        (
            pd.DataFrame(
                {"kind": ["merger"], "value": [1.0]}, index=pd.DatetimeIndex(["2024-01-01"])
            ),
            "unknown",
        ),
        (
            pd.DataFrame(
                {"kind": ["split"], "value": [0.0]}, index=pd.DatetimeIndex(["2024-01-01"])
            ),
            "positive",
        ),
        (
            pd.DataFrame(
                {"kind": ["split"], "value": [-2.0]}, index=pd.DatetimeIndex(["2024-01-01"])
            ),
            "positive",
        ),
    ],
)
def test_invalid_actions_are_rejected(frame, message) -> None:
    with pytest.raises(DataValidationError, match=message):
        normalize_actions(frame)


def test_missing_columns_are_rejected() -> None:
    with pytest.raises(DataValidationError, match="missing"):
        normalize_actions(
            pd.DataFrame({"value": [2.0]}, index=pd.DatetimeIndex(["2024-01-01"]))
            .assign(kind="split")
            .drop(columns="kind")
        )


def test_a_split_adjusts_only_earlier_bars(bars_factory) -> None:
    bars = bars_factory(10)  # 2024-01-01 .. 2024-01-10, close 100..109
    ex = "2024-01-06"
    out = adjust_bars(bars, _split(ex), pd.Timestamp("2024-02-01", tz="UTC"))

    before = bars.index < pd.Timestamp(ex, tz="UTC")
    assert (out.loc[before, "close"] == bars.loc[before, "close"] / 2).all()
    assert (out.loc[before, "volume"] == bars.loc[before, "volume"] * 2).all()
    assert (out.loc[~before, "close"] == bars.loc[~before, "close"]).all()
    assert (out["amount"] == bars["amount"]).all()  # traded value is invariant under a split
    assert out.attrs["adjusted_for"] == [pd.Timestamp(ex, tz="UTC").isoformat()]


@pytest.mark.leakage
def test_a_split_announced_after_as_of_is_not_applied(bars_factory) -> None:
    """A vendor series adjusted today must not leak a split that was not yet announced."""
    bars = bars_factory(10)
    split = _split("2024-01-06", announced="2024-03-01")
    out = adjust_bars(bars, split, pd.Timestamp("2024-02-01", tz="UTC"))
    assert (out["close"] == bars["close"]).all()


@pytest.mark.leakage
def test_a_split_not_yet_effective_at_as_of_is_not_applied(bars_factory) -> None:
    """Announced but with a future ex-date: prices up to as_of are still on the old basis."""
    bars = bars_factory(10)
    split = _split("2024-02-15", announced="2024-01-02")
    out = adjust_bars(bars, split, pd.Timestamp("2024-01-10", tz="UTC"))
    assert (out["close"] == bars["close"]).all()


@pytest.mark.leakage
def test_the_same_history_adjusts_differently_as_knowledge_grows(bars_factory) -> None:
    bars = bars_factory(10)
    split = _split("2024-01-06")
    early = adjust_bars(bars, split, pd.Timestamp("2024-01-05", tz="UTC"))
    late = adjust_bars(bars, split, pd.Timestamp("2024-01-31", tz="UTC"))
    assert (early["close"] == bars["close"]).all()
    assert late["close"].iloc[0] == bars["close"].iloc[0] / 2


def test_dividends_are_only_applied_on_request(bars_factory) -> None:
    bars = bars_factory(10)
    dividend = normalize_actions(
        pd.DataFrame(
            {"kind": ["dividend"], "value": [1.0]}, index=pd.DatetimeIndex(["2024-01-06"], tz="UTC")
        )
    )
    as_of = pd.Timestamp("2024-02-01", tz="UTC")
    assert (adjust_bars(bars, dividend, as_of)["close"] == bars["close"]).all()

    adjusted = adjust_bars(bars, dividend, as_of, adjust_dividends=True)
    prior_close = bars["close"].iloc[4]
    assert adjusted["close"].iloc[0] == pytest.approx(
        bars["close"].iloc[0] * (1 - 1.0 / prior_close)
    )
    assert adjusted["close"].iloc[-1] == bars["close"].iloc[-1]


def test_a_dividend_larger_than_the_price_is_rejected(bars_factory) -> None:
    bars = bars_factory(10)
    dividend = normalize_actions(
        pd.DataFrame(
            {"kind": ["dividend"], "value": [500.0]},
            index=pd.DatetimeIndex(["2024-01-06"], tz="UTC"),
        )
    )
    with pytest.raises(DataValidationError, match="not below"):
        adjust_bars(bars, dividend, pd.Timestamp("2024-02-01", tz="UTC"), adjust_dividends=True)


def test_an_action_before_all_bars_changes_nothing(bars_factory) -> None:
    bars = bars_factory(5)
    out = adjust_bars(bars, _split("2020-01-01"), pd.Timestamp("2024-02-01", tz="UTC"))
    assert (out["close"] == bars["close"]).all()


def test_no_actions_changes_nothing(bars_factory) -> None:
    bars = bars_factory(5)
    out = adjust_bars(bars, empty_actions(), pd.Timestamp("2024-02-01", tz="UTC"))
    assert (out["close"] == bars["close"]).all()
    assert out.attrs["adjusted_for"] == []


def test_cumulative_split_factor_respects_as_of() -> None:
    actions = pd.concat([_split("2024-01-05", 2.0), _split("2024-03-05", 3.0)]).sort_index()
    assert cumulative_split_factor(actions, pd.Timestamp("2024-01-01", tz="UTC")) == 1.0
    assert cumulative_split_factor(actions, pd.Timestamp("2024-02-01", tz="UTC")) == 2.0
    assert cumulative_split_factor(actions, pd.Timestamp("2024-04-01", tz="UTC")) == 6.0
