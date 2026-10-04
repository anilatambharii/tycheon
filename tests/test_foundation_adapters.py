"""TimesFM and Chronos adapters, against fakes with the real engines' call contracts.

The real engines need multi-hundred-megabyte downloads and are exercised by the slow
tests. These tests pin how the adapters *talk* to them: which arguments they pass, how
they map the output layout, and that revisions are pinned.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pandas as pd
import pytest

from tycheon.errors import ModelError, OptionalDependencyError
from tycheon.models.chronos import ChronosForecaster
from tycheon.models.chronos import forecaster as chronos_mod
from tycheon.models.timesfm import TimesFMForecaster
from tycheon.models.timesfm import forecaster as timesfm_mod


def _history(bars, n=300):
    history = bars.iloc[:n]
    return history, pd.Timestamp(history["available_at"].iloc[-1])


# ---------------------------------------------------------------------- TimesFM
def test_timesfm_maps_the_native_quantile_layout(fake_timesfm, syn_gbm) -> None:
    """Channel 0 is the mean; channels 1..9 are the 0.1..0.9 quantiles."""
    history, as_of = _history(syn_gbm)
    d = TimesFMForecaster(engine=fake_timesfm).predict(history, 6, 10, as_of)
    assert d.quantile_levels == (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
    assert d.quantiles.shape == (9, 6)
    assert d.samples is None and not d.has_paths
    assert set(d.extras) == {"mean"} and d.extras["mean"].shape == (6,)

    _, native = fake_timesfm.forecast(6, [history["close"].to_numpy()])
    np.testing.assert_allclose(d.quantiles, native[0, :, 1:].T)
    assert d.extras["mean"] == pytest.approx(native[0, :, 0])


def test_timesfm_only_sees_the_most_recent_context(fake_timesfm, syn_gbm) -> None:
    history, as_of = _history(syn_gbm, 400)
    d = TimesFMForecaster(engine=fake_timesfm, max_context=128).predict(history, 3, 1, as_of)
    assert fake_timesfm.calls == [(3, 128)]
    assert d.metadata.context_length_used == 128
    assert d.metadata.context_length_available == 400


def test_timesfm_refuses_a_horizon_beyond_what_is_compiled(fake_timesfm, syn_gbm) -> None:
    history, as_of = _history(syn_gbm)
    model = TimesFMForecaster(engine=fake_timesfm, max_horizon=16)
    with pytest.raises(ModelError, match="max_horizon=16"):
        model.predict(history, 17, 1, as_of)
    assert model.predict(history, 16, 1, as_of).horizon == 16


def test_timesfm_quantile_crossing_is_repaired_and_flagged(fake_timesfm_crossing, syn_gbm) -> None:
    history, as_of = _history(syn_gbm)
    d = TimesFMForecaster(engine=fake_timesfm_crossing).predict(history, 4, 1, as_of)
    assert (np.diff(d.quantiles, axis=0) >= 0).all()
    assert d.metadata.diagnostics["quantile_crossing_repaired"] is True


def test_timesfm_rejects_an_unexpected_output_layout(syn_gbm) -> None:
    class Wrong:
        def forecast(self, horizon, inputs):
            return np.zeros((1, horizon)), np.zeros((1, horizon, 3))

    history, as_of = _history(syn_gbm)
    with pytest.raises(ModelError, match="unexpected TimesFM quantile output"):
        TimesFMForecaster(engine=Wrong()).predict(history, 3, 1, as_of)


def test_timesfm_cannot_answer_path_dependent_questions(fake_timesfm, syn_gbm) -> None:
    history, as_of = _history(syn_gbm)
    d = TimesFMForecaster(engine=fake_timesfm).predict(history, 5, 1, as_of)
    with pytest.raises(ModelError, match="joint sample paths"):
        d.require_paths("max drawdown")
    lo, hi = d.interval(d.max_coverage)
    assert (lo <= hi).all() and d.max_coverage == pytest.approx(0.8)


def test_timesfm_loads_a_pinned_revision_and_compiles_for_prices(monkeypatch, syn_gbm) -> None:
    seen: dict[str, object] = {}

    class Engine:
        model = types.SimpleNamespace(device="cpu")

        def compile(self, config):
            seen["config"] = config

        def forecast(self, horizon, inputs):
            return np.ones((1, horizon)), np.ones((1, horizon, 10))

    class Model:
        @staticmethod
        def from_pretrained(repo, **kwargs):
            seen["repo"], seen["kwargs"] = repo, kwargs
            return Engine()

    fake = types.ModuleType("timesfm")
    fake.TimesFM_2p5_200M_torch = Model  # type: ignore[attr-defined]
    fake.ForecastConfig = types.SimpleNamespace  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "timesfm", fake)

    history, as_of = _history(syn_gbm)
    TimesFMForecaster(max_context=256, max_horizon=64).predict(history, 5, 1, as_of)

    assert seen["repo"] == "google/timesfm-2.5-200m-pytorch"
    assert seen["kwargs"] == {"revision": timesfm_mod.REVISION, "torch_compile": False}
    assert len(timesfm_mod.REVISION) == 40, "weights must be pinned to a full commit SHA"
    config = seen["config"]
    assert (config.max_context, config.max_horizon) == (256, 64)
    assert config.infer_is_positive is True, "prices are positive"
    assert config.fix_quantile_crossing is True and config.use_continuous_quantile_head is True


def test_timesfm_without_the_package_names_the_extra(monkeypatch, syn_gbm) -> None:
    monkeypatch.setitem(sys.modules, "timesfm", None)
    history, as_of = _history(syn_gbm)
    with pytest.raises(OptionalDependencyError, match=r"tycheon\[timesfm\]"):
        TimesFMForecaster().predict(history, 3, 1, as_of)


def test_timesfm_without_a_torch_backend_says_so(monkeypatch, syn_gbm) -> None:
    monkeypatch.setitem(sys.modules, "timesfm", types.ModuleType("timesfm"))
    history, as_of = _history(syn_gbm)
    with pytest.raises(OptionalDependencyError, match="PyTorch backend"):
        TimesFMForecaster().predict(history, 3, 1, as_of)


def test_timesfm_validates_its_arguments() -> None:
    with pytest.raises(ValueError, match="max_context"):
        TimesFMForecaster(max_context=4)
    with pytest.raises(ValueError, match="max_horizon"):
        TimesFMForecaster(max_horizon=0)


# ----------------------------------------------------------------------- Chronos
def test_chronos_maps_the_quantile_axis(fake_chronos, syn_gbm) -> None:
    history, as_of = _history(syn_gbm)
    d = ChronosForecaster(pipeline=fake_chronos).predict(history, 6, 10, as_of)
    assert d.quantile_levels == (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
    assert d.quantiles.shape == (7, 6)
    assert d.samples is None
    assert d.extras["mean"].shape == (6,)
    assert fake_chronos.calls == [(6, [0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95])]
    lo, hi = d.interval(0.9)  # 0.05..0.95 are native, so 90% works here
    assert (lo < hi).all()


def test_chronos_honours_custom_levels_and_context(fake_chronos, syn_gbm) -> None:
    history, as_of = _history(syn_gbm, 400)
    model = ChronosForecaster(
        pipeline=fake_chronos, quantile_levels=(0.2, 0.5, 0.8), max_context=100
    )
    d = model.predict(history, 3, 1, as_of)
    assert d.quantile_levels == (0.2, 0.5, 0.8)
    assert d.metadata.context_length_used == 100
    assert d.metadata.device == "injected"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"quantile_levels": ()}, "strictly inside"),
        ({"quantile_levels": (0.0, 0.5)}, "strictly inside"),
        ({"quantile_levels": (0.5, 1.0)}, "strictly inside"),
        ({"quantile_levels": (0.9, 0.1)}, "strictly increasing"),
        ({"quantile_levels": (0.5, 0.5)}, "strictly increasing"),
        ({"max_context": 4}, "max_context"),
    ],
)
def test_chronos_validates_its_arguments(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        ChronosForecaster(**kwargs)


def test_chronos_rejects_an_unexpected_output_shape(syn_gbm) -> None:
    class Wrong:
        def predict_quantiles(self, inputs, prediction_length, quantile_levels):
            return [np.zeros((2, prediction_length, len(quantile_levels)))], [
                np.zeros((2, prediction_length))
            ]

    history, as_of = _history(syn_gbm)
    with pytest.raises(ModelError, match="unexpected Chronos quantile output"):
        ChronosForecaster(pipeline=Wrong()).predict(history, 3, 1, as_of)


def test_chronos_loads_a_pinned_revision_on_the_requested_device(monkeypatch, syn_gbm) -> None:
    pytest.importorskip("torch")
    seen: dict[str, object] = {}

    class Pipeline:
        @staticmethod
        def from_pretrained(repo, **kwargs):
            seen["repo"], seen["kwargs"] = repo, kwargs
            return types.SimpleNamespace(
                predict_quantiles=lambda inputs, prediction_length, quantile_levels: (
                    [np.ones((1, prediction_length, len(quantile_levels)))],
                    [np.ones((1, prediction_length))],
                )
            )

    fake = types.ModuleType("chronos")
    fake.Chronos2Pipeline = Pipeline  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "chronos", fake)

    history, as_of = _history(syn_gbm)
    d = ChronosForecaster(device="cpu").predict(history, 4, 1, as_of)
    assert seen["repo"] == "amazon/chronos-2"
    assert seen["kwargs"] == {"revision": chronos_mod.REVISION, "device_map": "cpu"}
    assert len(chronos_mod.REVISION) == 40, "weights must be pinned to a full commit SHA"
    assert d.metadata.device == "cpu"
    assert d.metadata.model_version == chronos_mod.REVISION[:12]


def test_chronos_without_the_package_names_the_extra(monkeypatch, syn_gbm) -> None:
    monkeypatch.setitem(sys.modules, "chronos", None)
    history, as_of = _history(syn_gbm)
    with pytest.raises(OptionalDependencyError, match=r"tycheon\[chronos\]"):
        ChronosForecaster().predict(history, 3, 1, as_of)
