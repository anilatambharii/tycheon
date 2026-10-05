"""Real weights, real downloads. Excluded from ``make check``; run by ``make test-slow``.

Kronos-mini is small enough (a few MB of weights) to run on any CPU. TimesFM and
Chronos skip unless their extras are installed, because their weights are hundreds of
megabytes. All data here is synthetic.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd
import pytest

from tycheon.models.base import forecast_index

pytestmark = pytest.mark.slow


def _split(bars: pd.DataFrame, n: int):
    history = bars.iloc[:n]
    return history, pd.Timestamp(history["available_at"].iloc[-1])


@pytest.fixture(scope="module")
def mini():
    pytest.importorskip("torch")
    from tycheon.models.kronos import KronosForecaster

    return KronosForecaster("mini", device="cpu", seed=0).load()


def test_kronos_mini_loads_on_cpu_and_forecasts_sample_data(mini, syn_garch) -> None:
    history, as_of = _split(syn_garch, 900)
    d = mini.predict(history, 10, 16, as_of)

    assert d.samples is not None and d.samples.shape == (16, 10)
    assert np.isfinite(d.samples).all()
    assert len(np.unique(d.samples[:, -1])) > 1, "paths must not collapse to a single average"
    assert d.calibration_status == "uncalibrated"
    assert d.metadata.device == "cpu"
    assert d.metadata.context_length_used == 900  # mini reads up to 2048 bars
    assert not mini._model.training and not mini._tokenizer.training


def test_kronos_mini_is_pinned_to_the_released_commits(mini, syn_garch) -> None:
    history, as_of = _split(syn_garch, 200)
    d = mini.predict(history, 3, 4, as_of)
    assert (
        "f4e68697d9d5" in d.metadata.model_version  # pragma: allowlist secret
    )  # NeoQuasar/Kronos-mini  # pragma: allowlist secret
    assert (
        "26966d003506" in d.metadata.model_version
    )  # NeoQuasar/Kronos-Tokenizer-2k  # pragma: allowlist secret
    assert (
        d.metadata.params["upstream_commit"]
        == "67b630e67f6a18c9e9be918d9b4337c960db1e9a"  # pragma: allowlist secret
    )  # pragma: allowlist secret


def test_kronos_mini_is_reproducible_for_a_seed(mini, syn_garch) -> None:
    history, as_of = _split(syn_garch, 300)
    a = mini.predict(history, 5, 6, as_of, seed=21)
    b = mini.predict(history, 5, 6, as_of, seed=21)
    c = mini.predict(history, 5, 6, as_of, seed=22)
    np.testing.assert_array_equal(a.samples, b.samples)
    assert not np.array_equal(a.samples, c.samples)


def test_kronos_mini_sampler_matches_upstream_bit_for_bit_under_greedy_decoding(
    mini, syn_garch
) -> None:
    """The same exact-equality check as the fast suite, now on the released weights."""
    import torch

    from tycheon.models.kronos.sampling import sample_paths
    from tycheon.models.kronos.vendor import load_upstream
    from tycheon.models.kronos.windowing import calendar_features, window_context

    upstream = load_upstream()
    kmod = sys.modules["tycheon._vendor.kronos_model"]
    frame = syn_garch.iloc[:900]
    horizon = 10
    window = window_context(frame, max_context=mini.max_context)
    x = torch.from_numpy(window.normalized)[None]
    xs = torch.from_numpy(window.stamps)[None]
    ys = torch.from_numpy(calendar_features(forecast_index(frame.index, horizon)))[None]

    theirs = kmod.auto_regressive_inference(
        mini._tokenizer, mini._model, x, xs, ys, mini.max_context, horizon,
        clip=5, T=1.0, top_k=1, top_p=1.0, sample_count=1, verbose=False,
    )[:, -horizon:, :]  # fmt: skip
    ours = sample_paths(
        mini._tokenizer, mini._model, x, xs, ys,
        max_context=mini.max_context, pred_len=horizon, clip=5.0, temperature=1.0,
        top_k=1, top_p=1.0,
        generator=torch.Generator().manual_seed(0), filter_fn=upstream.top_k_top_p_filtering,
    ).numpy()  # fmt: skip
    assert np.abs(theirs - ours).max() == 0.0


def test_kronos_mini_batches_series_of_different_lengths(mini, syn_gbm, syn_garch) -> None:
    a, b = syn_gbm.iloc[:400], syn_garch.iloc[:250]
    as_of = max(pd.Timestamp(f["available_at"].iloc[-1]) for f in (a, b))
    first, second = mini.predict_batch([a, b], 5, 4, as_of)
    assert first.metadata.context_length_used == 400
    assert second.metadata.context_length_used == 250
    assert first.last_close == a["close"].iloc[-1]


def test_timesfm_real_weights(syn_gbm) -> None:
    pytest.importorskip("timesfm")
    pytest.importorskip("torch")
    from tycheon.models.timesfm import TimesFMForecaster

    history, as_of = _split(syn_gbm, 600)
    d = TimesFMForecaster(max_context=512, max_horizon=64).predict(history, 12, 1, as_of)
    assert d.quantiles.shape == (9, 12)
    assert np.isfinite(d.quantiles).all()
    assert (np.diff(d.quantiles, axis=0) >= 0).all()
    assert d.samples is None
    assert (d.median > 0).all()


def test_chronos_real_weights(syn_gbm) -> None:
    pytest.importorskip("chronos")
    pytest.importorskip("torch")
    from tycheon.models.chronos import ChronosForecaster

    history, as_of = _split(syn_gbm, 600)
    d = ChronosForecaster(device="cpu").predict(history, 12, 1, as_of)
    assert d.quantiles.shape == (7, 12)
    assert np.isfinite(d.quantiles).all()
    assert (np.diff(d.quantiles, axis=0) >= 0).all()
    assert d.samples is None
    assert (d.median > 0).all()
