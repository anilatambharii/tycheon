"""The bundled sample data is synthetic, deterministic, and valid by the same rules as real data."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tycheon.data import SAMPLE_DATASETS, SampleProvider, load_sample
from tycheon.data.sample import make_synthetic_bars, sample_end
from tycheon.data.schema import validate_bars
from tycheon.errors import ProviderError


def _mean_squared_return_acf(bars: pd.DataFrame, lags: int = 10) -> float:
    """Mean autocorrelation of squared log returns over lags 1..``lags``.

    Averaging over lags is steadier than lag 1 alone, which is noisy for fat-tailed
    series. Standard error is roughly 1 / sqrt(n) per lag (about 0.026 here).
    """
    r = np.diff(np.log(bars["close"].to_numpy()))
    sq = r**2 - np.mean(r**2)
    denom = float(np.sum(sq * sq))
    return float(np.mean([np.sum(sq[k:] * sq[:-k]) / denom for k in range(1, lags + 1)]))


@pytest.mark.parametrize("symbol", sorted(SAMPLE_DATASETS))
def test_every_sample_is_a_valid_bars_frame(symbol) -> None:
    bars = load_sample(symbol)
    validate_bars(bars)
    assert len(bars) == SAMPLE_DATASETS[symbol].periods
    assert (bars["high"] >= bars[["open", "close"]].max(axis=1)).all()
    assert (bars["low"] <= bars[["open", "close"]].min(axis=1)).all()
    assert (bars["volume"] > 0).all()


@pytest.mark.parametrize("symbol", sorted(SAMPLE_DATASETS))
def test_generation_is_deterministic(symbol) -> None:
    """Byte-for-byte reproducible is what makes redistributable-by-construction checkable."""
    spec = SAMPLE_DATASETS[symbol]
    pd.testing.assert_frame_equal(make_synthetic_bars(spec), make_synthetic_bars(spec))


def test_different_seeds_give_different_series() -> None:
    spec = SAMPLE_DATASETS["SYN-GBM"]
    other = make_synthetic_bars(type(spec)(**{**spec.__dict__, "seed": spec.seed + 1}))
    assert not np.allclose(make_synthetic_bars(spec)["close"], other["close"])


def test_the_regimes_have_the_shapes_they_claim() -> None:
    """Volatility clusters in the GARCH and regime series and not in the constant-volatility ones.

    Measured on these seeds: gbm 0.010, jump 0.002, garch 0.091, regime 0.163. The
    thresholds sit between the groups, not at the edge of either.
    """
    assert _mean_squared_return_acf(load_sample("SYN-GBM")) < 0.04
    assert _mean_squared_return_acf(load_sample("SYN-JUMP")) < 0.04
    assert _mean_squared_return_acf(load_sample("SYN-GARCH")) > 0.06
    assert _mean_squared_return_acf(load_sample("SYN-REGIME")) > 0.10


def test_the_jump_series_has_fat_tails() -> None:
    def excess_kurtosis(bars: pd.DataFrame) -> float:
        r = np.diff(np.log(bars["close"].to_numpy()))
        z = (r - r.mean()) / r.std()
        return float(np.mean(z**4) - 3.0)

    assert excess_kurtosis(load_sample("SYN-JUMP")) > excess_kurtosis(load_sample("SYN-GBM")) + 1.0


def test_the_hourly_sample_is_hourly() -> None:
    bars = load_sample("SYN-GBM-H")
    assert (bars.index[1:] - bars.index[:-1] == pd.Timedelta("1h")).all()


def test_load_sample_respects_as_of() -> None:
    cut = pd.Timestamp("2019-01-01", tz="UTC")
    bars = load_sample("SYN-GBM", as_of=cut)
    assert bars["available_at"].max() <= cut
    assert len(bars) < SAMPLE_DATASETS["SYN-GBM"].periods
    assert sample_end("SYN-GBM") == load_sample("SYN-GBM")["available_at"].max()


def test_unknown_samples_and_wrong_frequencies_are_errors() -> None:
    with pytest.raises(ProviderError, match="unknown sample symbol"):
        load_sample("AAPL")
    provider = SampleProvider()
    as_of = pd.Timestamp("2030-01-01", tz="UTC")
    with pytest.raises(ProviderError, match="unknown sample symbol"):
        provider.fetch_bars("AAPL", start=None, end=None, as_of=as_of)
    with pytest.raises(ProviderError, match="series, not"):
        provider.fetch_bars("SYN-GBM", start=None, end=None, as_of=as_of, frequency="1h")


def test_no_sample_looks_like_real_market_data() -> None:
    """Symbols are synthetic by name, so nothing here can be mistaken for a licensed series."""
    assert all(symbol.startswith("SYN-") for symbol in SAMPLE_DATASETS)
    assert "Synthetic" in SampleProvider.license_terms
