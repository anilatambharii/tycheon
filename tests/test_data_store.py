"""The as-of store: every read is point-in-time, history is append-only, paths cannot escape."""

from __future__ import annotations

import pandas as pd
import pytest

from tycheon.data import AsOfStore, SampleProvider
from tycheon.data.actions import normalize_actions
from tycheon.errors import DataValidationError, LookaheadError


@pytest.fixture
def store(tmp_path):
    return AsOfStore(tmp_path / "store")


def _utc(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


def test_round_trip(store, bars_factory) -> None:
    bars = bars_factory(20)
    store.put_bars("TEST", bars)
    out = store.bars("TEST", as_of=_utc("2030-01-01"))
    assert len(out) == 20
    assert list(out.columns) == ["open", "high", "low", "close", "volume", "amount", "available_at"]
    assert out["close"].tolist() == pytest.approx(bars["close"].tolist())
    assert store.symbols() == ["TEST"]


@pytest.mark.leakage
def test_bars_not_yet_available_do_not_exist_at_as_of(store, bars_factory) -> None:
    bars = bars_factory(20)
    store.put_bars("TEST", bars)
    as_of = pd.Timestamp(bars["available_at"].iloc[9])
    out = store.bars("TEST", as_of=as_of)
    assert len(out) == 10
    assert out["available_at"].max() <= as_of


@pytest.mark.leakage
def test_a_restatement_is_invisible_until_it_was_published(store, bars_factory) -> None:
    bars = bars_factory(5)
    store.put_bars("TEST", bars)
    revised = bars.iloc[[2]].copy()
    revised["close"] = 5000.0
    revised["high"] = 5001.0
    revised["available_at"] = _utc("2024-06-01")
    store.put_bars("TEST", revised)

    before = store.bars("TEST", as_of=_utc("2024-05-01"))
    after = store.bars("TEST", as_of=_utc("2024-07-01"))
    assert before["close"].iloc[2] == bars["close"].iloc[2]
    assert after["close"].iloc[2] == 5000.0
    assert len(before) == len(after) == 5


def test_the_store_is_append_only(store, bars_factory) -> None:
    store.put_bars("TEST", bars_factory(3))
    store.put_bars("TEST", bars_factory(3, start="2024-02-01"))
    files = list((store.root / "bars" / "symbol=TEST").glob("*.parquet"))
    assert len(files) == 2
    assert len(store.bars("TEST", as_of=_utc("2030-01-01"))) == 6


@pytest.mark.leakage
def test_a_window_ending_after_as_of_is_refused(store, bars_factory) -> None:
    store.put_bars("TEST", bars_factory(20))
    with pytest.raises(LookaheadError):
        store.bars("TEST", as_of=_utc("2024-01-10"), end=_utc("2024-01-11"))


def test_start_and_end_window_the_result(store, bars_factory) -> None:
    store.put_bars("TEST", bars_factory(20))
    out = store.bars(
        "TEST", as_of=_utc("2030-01-01"), start=_utc("2024-01-05"), end=_utc("2024-01-08")
    )
    assert out.index[0] == _utc("2024-01-05")
    assert out.index[-1] == _utc("2024-01-08")


def test_a_naive_as_of_is_refused(store, bars_factory) -> None:
    store.put_bars("TEST", bars_factory(3))
    with pytest.raises(DataValidationError, match="timezone-aware"):
        store.bars("TEST", as_of=pd.Timestamp("2030-01-01"))


def test_an_unknown_symbol_is_an_error_not_an_empty_frame(store) -> None:
    with pytest.raises(DataValidationError, match="no bars stored"):
        store.bars("NOPE", as_of=_utc("2030-01-01"))


@pytest.mark.parametrize("symbol", ["../outside", "a/b", "..", "C:\\x"])
def test_path_traversal_through_a_symbol_is_blocked(store, bars_factory, symbol) -> None:
    with pytest.raises(DataValidationError, match="invalid symbol"):
        store.put_bars(symbol, bars_factory(3))
    with pytest.raises(DataValidationError, match="invalid symbol"):
        store.bars(symbol, as_of=_utc("2030-01-01"))
    assert not (store.root.parent / "outside").exists()


def test_bars_without_availability_cannot_be_stored(store, bars_factory) -> None:
    with pytest.raises(DataValidationError, match="available_at"):
        store.put_bars("TEST", bars_factory(3).drop(columns="available_at"))


def test_bars_that_fail_validation_are_not_written(store, bars_factory) -> None:
    bad = bars_factory(3).assign(close=-1.0)
    with pytest.raises(DataValidationError):
        store.put_bars("TEST", bad)
    assert store.symbols() == []


def test_a_path_with_a_quote_in_it_cannot_break_the_query(tmp_path, bars_factory) -> None:
    """Paths are interpolated into SQL as literals; a quote in the root must be escaped."""
    store = AsOfStore(tmp_path / "it's here")
    store.put_bars("TEST", bars_factory(3))
    assert len(store.bars("TEST", as_of=_utc("2030-01-01"))) == 3


def test_ingest_pulls_bars_through_the_provider_guard(store) -> None:
    n = store.ingest(SampleProvider(), "SYN-GBM", start=None, end=None, as_of=_utc("2019-06-30"))
    out = store.bars("SYN-GBM", as_of=_utc("2019-06-30"))
    assert n == len(out) > 300
    assert out["available_at"].max() <= _utc("2019-06-30")


@pytest.mark.leakage
def test_ingest_does_not_trust_a_provider_that_returns_the_future(store, bars_factory) -> None:
    """A provider that is not a ProviderBase has no guard of its own; the store must check."""

    class Leaky:
        name = "leaky"
        license_terms = "n/a"
        commercial_use = True

        def fetch_bars(self, symbol, *, start, end, as_of, frequency="1D"):
            return bars_factory(30)

        def fetch_corporate_actions(self, symbol, *, as_of):
            return pd.DataFrame()

    with pytest.raises(LookaheadError):
        store.ingest(Leaky(), "TEST", start=None, end=None, as_of=_utc("2024-01-05"))
    assert store.symbols() == []


def test_corporate_actions_round_trip_and_adjust_point_in_time(store, bars_factory) -> None:
    store.put_bars("TEST", bars_factory(10))
    split = normalize_actions(
        pd.DataFrame(
            {"kind": ["split"], "value": [2.0]}, index=pd.DatetimeIndex(["2024-01-06"], tz="UTC")
        )
    )
    store.put_actions("TEST", split)

    assert len(store.actions("TEST", as_of=_utc("2030-01-01"))) == 1
    assert store.actions("TEST", as_of=_utc("2024-01-05")).empty  # not announced yet

    raw = store.bars("TEST", as_of=_utc("2030-01-01"))
    early = store.adjusted_bars("TEST", as_of=_utc("2024-01-05"))
    late = store.adjusted_bars("TEST", as_of=_utc("2030-01-01"))
    assert early["close"].iloc[0] == raw["close"].iloc[0]
    assert late["close"].iloc[0] == pytest.approx(raw["close"].iloc[0] / 2)


def test_actions_for_a_symbol_without_any_are_empty(store, bars_factory) -> None:
    store.put_bars("TEST", bars_factory(3))
    assert store.actions("TEST", as_of=_utc("2030-01-01")).empty


def test_an_empty_store_lists_no_symbols(tmp_path) -> None:
    assert AsOfStore(tmp_path / "nothing").symbols() == []
