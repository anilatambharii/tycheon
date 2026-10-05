"""Shared test setup and fixtures.

The benchmark harness lives at the repo root rather than inside the installed
package, so make the root importable for the tests that cover it.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tycheon.data import load_sample
from tycheon.data.schema import normalize_bars

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BarsFactory = Callable[..., pd.DataFrame]
HistoryAt = Callable[[pd.DataFrame, int], tuple[pd.DataFrame, pd.Timestamp]]


@pytest.fixture(scope="session")
def syn_gbm() -> pd.DataFrame:
    return load_sample("SYN-GBM")


@pytest.fixture(scope="session")
def syn_garch() -> pd.DataFrame:
    return load_sample("SYN-GARCH")


@pytest.fixture
def history_at() -> HistoryAt:
    """Cut a bars frame to the first ``n`` bars and the ``as_of`` at which they were known."""

    def cut(bars: pd.DataFrame, n: int) -> tuple[pd.DataFrame, pd.Timestamp]:
        return bars.iloc[:n].copy(), pd.Timestamp(bars["available_at"].iloc[n - 1])

    return cut


@pytest.fixture
def bars_factory() -> BarsFactory:
    """Small deterministic bars frames for tests that need exact values."""

    def make(
        n: int = 10,
        *,
        start: str = "2024-01-01",
        close0: float = 100.0,
        step: float = 1.0,
        freq: str = "1D",
        volume: float | None = 1000.0,
    ) -> pd.DataFrame:
        index = pd.date_range(start, periods=n, freq=freq, tz="UTC")
        close = close0 + step * np.arange(n, dtype=float)
        frame = pd.DataFrame(
            {"open": close - 0.1, "high": close + 0.5, "low": close - 0.5, "close": close},
            index=index,
        )
        if volume is not None:
            frame["volume"] = volume
            frame["amount"] = volume * close
        return normalize_bars(frame, bar_duration=pd.Timedelta(freq))

    return make


# --------------------------------------------------------------------- fakes
class FakeTimesFMEngine:
    """Stands in for a compiled TimesFM model: same ``forecast`` contract, no weights.

    Returns ``(point, quantiles)`` with quantiles shaped ``(series, horizon, 10)``:
    channel 0 is the mean and channels 1..9 the 0.1..0.9 quantiles, as in TimesFM 2.5.
    """

    def __init__(self, crossing: bool = False) -> None:
        import types

        self.model = types.SimpleNamespace(device="cpu")
        self.crossing = crossing
        self.calls: list[tuple[int, int]] = []

    def forecast(self, horizon: int, inputs: list[np.ndarray]):
        z = np.array([-1.2816, -0.8416, -0.5244, -0.2533, 0.0, 0.2533, 0.5244, 0.8416, 1.2816])
        steps = np.arange(1, horizon + 1, dtype=float)
        self.calls.append((horizon, len(inputs[0])))
        points, quants = [], []
        for series in inputs:
            last = float(series[-1])
            mean = last * (1.0 + 0.0005 * steps)
            spread = last * 0.01 * np.sqrt(steps)
            channels = [mean] + [mean + zk * spread for zk in z]
            q = np.stack(channels, axis=-1)
            if self.crossing:
                q[:, [3, 4]] = q[:, [4, 3]]
            points.append(mean)
            quants.append(q)
        return np.stack(points), np.stack(quants)


class FakeChronosPipeline:
    """Stands in for ``Chronos2Pipeline``: same ``predict_quantiles`` contract, no weights."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, list[float]]] = []

    def predict_quantiles(self, inputs, prediction_length, quantile_levels):
        from scipy.stats import norm

        self.calls.append((prediction_length, list(quantile_levels)))
        z = norm.ppf(np.asarray(quantile_levels))
        steps = np.arange(1, prediction_length + 1, dtype=float)
        quantiles, means = [], []
        for series in inputs:
            last = float(np.asarray(series)[-1])
            mean = last * (1.0 + 0.0003 * steps)
            spread = last * 0.012 * np.sqrt(steps)
            quantiles.append(mean[None, :, None] + z[None, None, :] * spread[None, :, None])
            means.append(mean[None, :])
        return quantiles, means


@pytest.fixture
def fake_timesfm() -> FakeTimesFMEngine:
    return FakeTimesFMEngine()


@pytest.fixture
def fake_timesfm_crossing() -> FakeTimesFMEngine:
    return FakeTimesFMEngine(crossing=True)


@pytest.fixture
def fake_chronos() -> FakeChronosPipeline:
    return FakeChronosPipeline()


TINY_CONTEXT = 64


@pytest.fixture(scope="session")
def tiny_kronos():
    """A randomly-initialised, miniature upstream Kronos (tokenizer, model, max_context).

    Built from the vendored upstream classes, so it runs the same code path as the real
    model, with no download. Its forecasts are noise; it exists to test plumbing.
    """
    torch = pytest.importorskip("torch")
    from tycheon.models.kronos.vendor import load_upstream

    upstream = load_upstream()
    torch.manual_seed(0)
    tokenizer = upstream.tokenizer_cls(
        d_in=6,
        d_model=16,
        n_heads=2,
        ff_dim=32,
        n_enc_layers=1,
        n_dec_layers=1,
        ffn_dropout_p=0.0,
        attn_dropout_p=0.0,
        resid_dropout_p=0.0,
        s1_bits=4,
        s2_bits=4,
        beta=0.05,
        gamma0=1.0,
        gamma=1.1,
        zeta=0.05,
        group_size=2,
    ).eval()
    model = upstream.kronos_cls(
        s1_bits=4,
        s2_bits=4,
        n_layers=1,
        d_model=16,
        n_heads=2,
        ff_dim=32,
        ffn_dropout_p=0.0,
        attn_dropout_p=0.0,
        resid_dropout_p=0.0,
        token_dropout_p=0.0,
        learn_te=True,
    ).eval()
    return tokenizer, model, TINY_CONTEXT
