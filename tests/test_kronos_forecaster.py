"""KronosForecaster, run through the real vendored code on a tiny random model."""

from __future__ import annotations

import sys
import types

import numpy as np
import pandas as pd
import pytest

from tycheon.errors import DataValidationError, LookaheadError, ModelError, OptionalDependencyError
from tycheon.models.base import forecast_index
from tycheon.models.devices import resolve_device
from tycheon.models.kronos import KRONOS_SPECS, KronosForecaster
from tycheon.models.kronos.sampling import sample_paths, validate_sampling
from tycheon.models.kronos.vendor import load_upstream
from tycheon.models.kronos.windowing import calendar_features, window_context

torch = pytest.importorskip("torch")


@pytest.fixture
def kronos(tiny_kronos):
    tokenizer, model, max_context = tiny_kronos
    return KronosForecaster(
        "small", tokenizer=tokenizer, model=model, max_context=max_context, device="cpu", seed=0
    )


def _history(bars, n=300):
    history = bars.iloc[:n]
    return history, pd.Timestamp(history["available_at"].iloc[-1])


# ------------------------------------------------------------------ the registry
def test_the_released_checkpoints_are_pinned_to_commits_and_sized_per_upstream() -> None:
    """Facts from the upstream README and Hugging Face cards, read 2026-10-04."""
    assert set(KRONOS_SPECS) == {"mini", "small", "base"}, "Kronos-large is not public"
    assert KRONOS_SPECS["mini"].max_context == 2048
    assert KRONOS_SPECS["small"].max_context == 512
    assert KRONOS_SPECS["base"].max_context == 512
    assert KRONOS_SPECS["mini"].tokenizer_id == "NeoQuasar/Kronos-Tokenizer-2k"
    assert KRONOS_SPECS["small"].tokenizer_id == "NeoQuasar/Kronos-Tokenizer-base"
    assert KRONOS_SPECS["base"].tokenizer_id == "NeoQuasar/Kronos-Tokenizer-base"
    for spec in KRONOS_SPECS.values():
        assert len(spec.model_revision) == 40, "weights must be pinned to a full commit SHA"
        assert len(spec.tokenizer_revision) == 40
        assert spec.model_card == f"docs/models/kronos-{spec.variant}.md"
        assert spec.model_revision[:12] in spec.version


# ------------------------------------------------------------------ construction
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"variant": "large"}, "unknown Kronos variant"),
        ({"temperature": 0.0}, "temperature"),
        ({"temperature": -1.0}, "temperature"),
        ({"top_p": 0.0}, "top_p"),
        ({"top_p": 1.5}, "top_p"),
        ({"top_k": -1}, "top_k"),
        ({"top_k": 5, "top_p": 0.9}, "cannot be combined"),
        ({"max_batch_rows": 0}, "max_batch_rows"),
        ({"lookback": 4}, "lookback"),
    ],
)
def test_bad_settings_are_rejected_up_front(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        KronosForecaster(**kwargs)


def test_injection_is_all_or_nothing(tiny_kronos) -> None:
    tokenizer, model, max_context = tiny_kronos
    with pytest.raises(ValueError, match="injected together"):
        KronosForecaster("small", tokenizer=tokenizer)
    with pytest.raises(ValueError, match="injected together"):
        KronosForecaster("small", model=model, max_context=max_context)


def test_top_k_alone_and_top_p_alone_are_both_allowed() -> None:
    validate_sampling(1.0, 5, 1.0)
    validate_sampling(1.0, 0, 0.9)
    validate_sampling(0.7, 0, 1.0)


# ---------------------------------------------------------------------- forecasts
def test_it_returns_unaveraged_sample_paths(kronos, syn_gbm) -> None:
    """Upstream averages its samples into one path; Tycheon must keep every one."""
    history, as_of = _history(syn_gbm)
    d = kronos.predict(history, 6, 20, as_of)
    assert d.samples is not None and d.samples.shape == (20, 6)
    assert len(np.unique(d.samples[:, -1])) > 1, "rows are identical: samples were collapsed"
    assert d.samples[:, -1].std() > 0
    assert d.has_paths


def test_extras_carry_the_other_channels(kronos, syn_gbm) -> None:
    history, as_of = _history(syn_gbm)
    d = kronos.predict(history, 4, 8, as_of)
    assert set(d.extras) == {"open", "high", "low", "volume", "amount"}
    assert all(v.shape == (8, 4) for v in d.extras.values())


def test_provenance_in_the_metadata(kronos, syn_gbm) -> None:
    history, as_of = _history(syn_gbm, 300)
    d = kronos.predict(history, 5, 10, as_of)
    meta = d.metadata
    assert meta.model_id == "kronos-small"
    assert meta.model_version == "injected"
    assert meta.context_length_available == 300
    assert meta.context_length_used == 64, "history longer than the context must be cut"
    assert meta.diagnostics["context_truncated"] is True
    assert (
        meta.params["upstream_commit"]
        == "67b630e67f6a18c9e9be918d9b4337c960db1e9a"  # pragma: allowlist secret
    )  # pragma: allowlist secret
    assert meta.params["top_p"] == 0.9 and meta.params["temperature"] == 1.0
    assert meta.device == "cpu"


def test_a_short_history_is_not_marked_truncated(kronos, syn_gbm) -> None:
    history, as_of = _history(syn_gbm, 40)
    d = kronos.predict(history, 4, 6, as_of)
    assert d.metadata.context_length_used == 40
    assert d.metadata.diagnostics["context_truncated"] is False


def test_weights_are_in_eval_mode_after_loading(tiny_kronos, syn_gbm) -> None:
    """Upstream never calls eval(); dropout left on would make forecasts noisy."""
    tokenizer, model, max_context = tiny_kronos
    model.train()
    tokenizer.train()
    forecaster = KronosForecaster(
        "small", tokenizer=tokenizer, model=model, max_context=max_context, device="cpu"
    )
    forecaster.load()
    assert not model.training and not tokenizer.training
    assert all(not p.requires_grad for p in model.parameters())
    assert forecaster.load() is forecaster


def test_seed_none_draws_a_fresh_stream_each_call_and_records_it(kronos, syn_gbm) -> None:
    history, as_of = _history(syn_gbm)
    a = kronos.predict(history, 4, 10, as_of, seed=None) if kronos.seed is None else None
    unseeded = KronosForecaster(
        "small",
        tokenizer=kronos._tokenizer,
        model=kronos._model,
        max_context=kronos.max_context,
        device="cpu",
        seed=None,
    )
    first = unseeded.predict(history, 4, 10, as_of)
    second = unseeded.predict(history, 4, 10, as_of)
    assert first.metadata.seed is None
    assert isinstance(first.metadata.params["sampler_seed"], int)
    assert not np.array_equal(first.samples, second.samples)
    assert a is None


def test_volume_and_amount_are_optional(kronos, syn_gbm) -> None:
    history, as_of = _history(syn_gbm, 120)
    ohlc = history[["open", "high", "low", "close", "available_at"]]
    assert kronos.predict(ohlc, 3, 4, as_of).samples.shape == (4, 3)
    no_amount = history.drop(columns="amount")
    assert kronos.predict(no_amount, 3, 4, as_of).samples.shape == (4, 3)


def test_the_horizon_may_not_exceed_the_context(kronos, syn_gbm) -> None:
    """Upstream decodes only the last max_context tokens; a longer horizon gets the wrong shape."""
    history, as_of = _history(syn_gbm)
    with pytest.raises(ModelError, match="exceeds the 64-bar context"):
        kronos.predict(history, 65, 4, as_of)
    assert kronos.predict(history, 64, 2, as_of).horizon == 64


def test_a_horizon_that_rolls_the_context_window_works(kronos, syn_gbm) -> None:
    history, as_of = _history(syn_gbm, 60)  # 60 + 20 > 64: the buffer must roll
    d = kronos.predict(history, 20, 4, as_of)
    assert d.samples.shape == (4, 20)
    assert np.isfinite(d.samples).all()


def test_too_little_history_is_refused(kronos, syn_gbm) -> None:
    history, as_of = _history(syn_gbm, 10)
    with pytest.raises(DataValidationError, match="need at least 16"):
        kronos.predict(history, 3, 4, as_of)


@pytest.mark.leakage
def test_it_refuses_a_history_from_after_as_of(kronos, syn_gbm) -> None:
    history, _ = _history(syn_gbm)
    with pytest.raises(LookaheadError):
        kronos.predict(history, 5, 4, pd.Timestamp(history["available_at"].iloc[100]))


def test_lookback_limits_the_context(tiny_kronos, syn_gbm) -> None:
    tokenizer, model, max_context = tiny_kronos
    limited = KronosForecaster(
        "small",
        tokenizer=tokenizer,
        model=model,
        max_context=max_context,
        device="cpu",
        lookback=32,
    )
    history, as_of = _history(syn_gbm)
    assert limited.predict(history, 3, 4, as_of).metadata.context_length_used == 32


# ----------------------------------------------------------------------- batching
def test_a_batch_of_one_equals_a_single_prediction(kronos, syn_gbm) -> None:
    history, as_of = _history(syn_gbm, 120)
    single = kronos.predict(history, 5, 6, as_of, seed=9)
    batched = kronos.predict_batch([history], 5, 6, as_of, seed=9)
    assert len(batched) == 1
    np.testing.assert_array_equal(single.samples, batched[0].samples)


def test_series_of_different_lengths_are_batched_and_returned_in_order(
    kronos, syn_gbm, syn_garch
) -> None:
    a = syn_gbm.iloc[:120]
    b = syn_garch.iloc[:80]
    c = syn_gbm.iloc[200:320]  # same length as a
    as_of = max(pd.Timestamp(f["available_at"].iloc[-1]) for f in (a, b, c))
    results = kronos.predict_batch([a, b, c], 4, 5, as_of)
    assert [r.metadata.context_length_used for r in results] == [64, 64, 64]
    assert [r.last_close for r in results] == [
        a["close"].iloc[-1],
        b["close"].iloc[-1],
        c["close"].iloc[-1],
    ]
    assert all(r.samples.shape == (5, 4) for r in results)

    short = syn_gbm.iloc[:30]
    mixed = kronos.predict_batch([a, short], 4, 5, as_of)
    assert [r.metadata.context_length_used for r in mixed] == [64, 30]


def test_batch_validation_applies_to_every_series(kronos, syn_gbm) -> None:
    good, as_of = _history(syn_gbm, 100)
    late = syn_gbm.iloc[:200]
    with pytest.raises(LookaheadError):
        kronos.predict_batch([good, late], 3, 4, as_of)
    with pytest.raises(ValueError, match="n_samples"):
        kronos.predict_batch([good], 3, 0, as_of)


def test_small_batches_cover_every_sample(tiny_kronos, syn_gbm) -> None:
    tokenizer, model, max_context = tiny_kronos
    chunked = KronosForecaster(
        "small",
        tokenizer=tokenizer,
        model=model,
        max_context=max_context,
        device="cpu",
        max_batch_rows=3,
    )
    history, as_of = _history(syn_gbm, 100)
    d = chunked.predict(history, 3, 10, as_of)  # 10 samples in chunks of 3
    assert d.samples.shape == (10, 3)
    assert np.isfinite(d.samples).all()


# ------------------------------------------------------- fidelity to upstream
def _greedy_compare(tiny_kronos, frame, horizon: int) -> float:
    """Max |upstream - ours| for greedy decoding, where randomness cannot differ."""
    tokenizer, model, max_context = tiny_kronos
    upstream = load_upstream()
    kmod = sys.modules["tycheon._vendor.kronos_model"]

    window = window_context(frame, max_context=max_context)
    future = calendar_features(forecast_index(frame.index, horizon))
    x = torch.from_numpy(window.normalized)[None]
    xs = torch.from_numpy(window.stamps)[None]
    ys = torch.from_numpy(future)[None]

    theirs = kmod.auto_regressive_inference(
        tokenizer, model, x, xs, ys, max_context, horizon,
        clip=5, T=1.0, top_k=1, top_p=1.0, sample_count=1, verbose=False,
    )[:, -horizon:, :]  # fmt: skip
    ours = sample_paths(
        tokenizer, model, x, xs, ys,
        max_context=max_context, pred_len=horizon, clip=5.0, temperature=1.0, top_k=1, top_p=1.0,
        generator=torch.Generator().manual_seed(0), filter_fn=upstream.top_k_top_p_filtering,
    ).numpy()  # fmt: skip
    assert theirs.shape == ours.shape
    return float(np.abs(theirs - ours).max())


def test_the_sampler_matches_upstream_exactly_under_greedy_decoding(tiny_kronos, syn_gbm) -> None:
    """With top_k=1 there is no randomness, so a faithful port must agree to the bit.

    Checked with a context that fits and with one long enough to roll the window.
    The same comparison against the real Kronos-mini weights is in the slow tests.
    """
    assert _greedy_compare(tiny_kronos, syn_gbm.iloc[:60], 6) == 0.0
    assert _greedy_compare(tiny_kronos, syn_gbm.iloc[:200], 20) == 0.0


# ----------------------------------------------------------------------- devices
def _fake_torch(cuda: bool = False, mps: bool = False):
    return types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: cuda),
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps)),
    )


@pytest.mark.parametrize(
    ("cuda", "mps", "expected"),
    [(True, True, "cuda:0"), (False, True, "mps"), (False, False, "cpu")],
)
def test_auto_prefers_cuda_then_mps_then_cpu(cuda, mps, expected) -> None:
    assert resolve_device("auto", _fake_torch(cuda, mps)) == expected


def test_explicit_devices_are_honoured_or_refused_loudly() -> None:
    assert resolve_device("CPU", _fake_torch()) == "cpu"
    assert resolve_device("cuda", _fake_torch(cuda=True)) == "cuda:0"
    assert resolve_device("cuda:1", _fake_torch(cuda=True)) == "cuda:1"
    assert resolve_device("mps", _fake_torch(mps=True)) == "mps"
    with pytest.raises(ModelError, match="CUDA is not available"):
        resolve_device("cuda", _fake_torch())
    with pytest.raises(ModelError, match="MPS is not available"):
        resolve_device("mps", _fake_torch())
    with pytest.raises(ValueError, match="unknown device"):
        resolve_device("tpu", _fake_torch())


def test_a_torch_build_without_mps_support_is_handled() -> None:
    no_backend = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False), backends=types.SimpleNamespace()
    )
    assert resolve_device("auto", no_backend) == "cpu"
    with pytest.raises(ModelError):
        resolve_device("mps", no_backend)


def test_a_missing_torch_gives_an_actionable_error(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(OptionalDependencyError, match=r"tycheon\[kronos\]"):
        KronosForecaster("small").load()
