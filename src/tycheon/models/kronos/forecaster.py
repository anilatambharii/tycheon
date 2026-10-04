"""The Kronos forecaster: sample paths from the upstream foundation model.

What it adds on top of upstream Kronos, and why (see ADR 0002 and the model cards):

* **Distributions, not averages.** Upstream averages its sample paths; this keeps them,
  so a forecast carries quantiles *and* joint paths.
* **Point-in-time.** The history is checked against ``as_of`` before the model runs.
* **A documented context policy.** Histories longer than the model context are cut to
  the most recent bars *before* normalisation (see :mod:`.windowing`).
* **Reproducibility.** An explicit seed drives an explicit generator.
* **Batching.** Many series, and many samples per series, run through the model in
  chunks of ``max_batch_rows`` so memory stays bounded.

Zero-shot Kronos knows nothing about calibration. Its sample spread is whatever the
pre-trained model believes, and until Phase T2 every forecast from this class is
labelled ``uncalibrated``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from tycheon.errors import ModelError, OptionalDependencyError
from tycheon.models.base import (
    BaseForecaster,
    ForecastDistribution,
    PreparedHistory,
    RawForecast,
    _positive_int,
    prepare_history,
)
from tycheon.models.devices import resolve_device
from tycheon.models.kronos.sampling import sample_paths, validate_sampling
from tycheon.models.kronos.specs import KRONOS_SPECS, KronosSpec, Variant
from tycheon.models.kronos.vendor import Upstream, load_upstream
from tycheon.models.kronos.windowing import (
    CLOSE_INDEX,
    FEATURES,
    ContextWindow,
    calendar_features,
    window_context,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    import pandas as pd


def _import_torch() -> Any:
    try:
        import torch
    except ImportError as exc:
        raise OptionalDependencyError(
            "Kronos needs PyTorch. Install the extra: pip install 'tycheon[kronos]'"
        ) from exc
    return torch


class KronosForecaster(BaseForecaster):
    """Zero-shot Kronos (mini, small or base) producing sample paths of the close price.

    Args:
        variant: Which released checkpoint: ``"mini"`` (2048-bar context), ``"small"``
            or ``"base"`` (512-bar context). ``Kronos-large`` is not public.
        device: ``"auto"`` (CUDA, then MPS, then CPU), ``"cpu"``, ``"cuda"`` or ``"mps"``.
        lookback: Use at most this many recent bars. Defaults to the full context.
        temperature, top_k, top_p: Sampling settings, as in upstream. ``top_k`` and
            ``top_p`` cannot be combined (upstream ignores ``top_p`` when ``top_k > 0``).
        clip: Normalised inputs are clipped to ``+-clip``.
        max_batch_rows: Maximum paths pushed through the model at once.
        seed: Seeds the sampler. ``None`` draws a fresh seed each call.
        tokenizer, model, max_context: Inject already-built upstream objects (used by the
            tests with a tiny randomly-initialised model); all three must be given.

    Weights are downloaded from the Hugging Face Hub on first use, pinned to the exact
    commits in :mod:`.specs`.
    """

    supports_paths = True
    required_columns = ("open", "high", "low", "close")
    min_history = 16

    def __init__(
        self,
        variant: Variant = "small",
        *,
        device: str = "auto",
        lookback: int | None = None,
        temperature: float = 1.0,
        top_k: int = 0,
        top_p: float = 0.9,
        clip: float = 5.0,
        max_batch_rows: int = 64,
        seed: int | None = 0,
        tokenizer: Any | None = None,
        model: Any | None = None,
        max_context: int | None = None,
    ) -> None:
        super().__init__(seed=seed)
        if variant not in KRONOS_SPECS:
            raise ValueError(
                f"unknown Kronos variant {variant!r}; choose from {sorted(KRONOS_SPECS)}"
            )
        validate_sampling(temperature, top_k, top_p)
        if max_batch_rows < 1:
            raise ValueError("max_batch_rows must be at least 1")
        if lookback is not None and lookback < self.min_history:
            raise ValueError(f"lookback must be at least {self.min_history}")

        self.spec: KronosSpec = KRONOS_SPECS[variant]
        self.model_id = self.spec.forecaster_id
        self.model_card = self.spec.model_card
        self.device_request = device
        self.lookback = lookback
        self.temperature = temperature
        self.top_k = top_k
        self.top_p = top_p
        self.clip = clip
        self.max_batch_rows = max_batch_rows

        injected = (tokenizer is not None, model is not None, max_context is not None)
        if any(injected) and not all(injected):
            raise ValueError("tokenizer, model and max_context must be injected together")
        self._injected = all(injected)
        if self._injected:
            if max_context is None:  # pragma: no cover - guarded by the all() check above
                raise ValueError("max_context is required with an injected model")
            self.max_context = max_context
        else:
            self.max_context = self.spec.max_context
        self._tokenizer = tokenizer
        self._model = model
        self._device: str | None = None
        self._upstream: Upstream | None = None

    # ------------------------------------------------------------------ loading
    def load(self) -> KronosForecaster:
        """Download (if needed) and initialise the weights. Called lazily by ``predict``."""
        if self._device is not None:
            return self
        torch = _import_torch()
        device = resolve_device(self.device_request, torch)
        upstream = load_upstream()

        tokenizer, model = self._tokenizer, self._model
        if tokenizer is None or model is None:
            tokenizer = upstream.tokenizer_cls.from_pretrained(
                self.spec.tokenizer_id, revision=self.spec.tokenizer_revision
            )
            model = upstream.kronos_cls.from_pretrained(
                self.spec.model_id, revision=self.spec.model_revision
            )
        # Upstream never calls eval(); dropout left on would make forecasts noisier
        # and non-reproducible, so it is switched off explicitly.
        self._tokenizer = tokenizer.to(device).eval()
        self._model = model.to(device).eval()
        for module in (self._tokenizer, self._model):
            for parameter in module.parameters():
                parameter.requires_grad_(False)
        self._upstream = upstream
        self._device = device
        return self

    # --------------------------------------------------------------- forecasting
    def _forecast(
        self, prepared: PreparedHistory, horizon: int, n_samples: int, seed: int | None
    ) -> RawForecast:
        return self._run([prepared], horizon, n_samples, seed)[0]

    def predict_batch(
        self,
        histories: Sequence[pd.DataFrame],
        horizon: int,
        n_samples: int,
        as_of: datetime,
        *,
        seed: int | None = None,
    ) -> list[ForecastDistribution]:
        """Forecast several series at once, in the same order as ``histories``.

        Series of equal context length share forward passes; differing lengths are
        grouped automatically (upstream refuses them). The random stream is shared
        across the call, so a series' paths depend on which others were batched with
        it; call :meth:`predict` per series if you need that independence.
        """
        n_samples = _positive_int(n_samples, "n_samples")
        prepared = [
            prepare_history(
                h, horizon, as_of, required=self.required_columns, min_length=self.min_history
            )
            for h in histories
        ]
        used_seed = self.seed if seed is None else seed
        raws = self._run(prepared, int(horizon), int(n_samples), used_seed)
        return [
            self._package(p, int(horizon), used_seed, raw)
            for p, raw in zip(prepared, raws, strict=True)
        ]

    def _run(
        self, prepared: Sequence[PreparedHistory], horizon: int, n_samples: int, seed: int | None
    ) -> list[RawForecast]:
        if horizon > self.max_context:
            raise ModelError(
                f"horizon {horizon} exceeds the {self.max_context}-bar context of {self.model_id}; "
                "upstream decodes only the last max_context tokens, so a longer horizon would "
                "silently return the wrong shape"
            )
        self.load()
        import torch

        if self._device is None or self._upstream is None:  # pragma: no cover
            raise ModelError("Kronos failed to initialise")
        device = self._device
        generator = torch.Generator(device=device)
        used_seed = int(generator.seed()) if seed is None else seed
        generator.manual_seed(used_seed)

        windows = [
            window_context(
                p.frame, max_context=self.max_context, lookback=self.lookback, clip=self.clip
            )
            for p in prepared
        ]
        futures = [calendar_features(p.future_index) for p in prepared]

        # Equal-length contexts can share a forward pass; group them.
        by_length: dict[int, list[int]] = {}
        for position, window in enumerate(windows):
            by_length.setdefault(window.length, []).append(position)

        decoded: dict[int, np.ndarray[Any, np.dtype[np.float32]]] = {}
        for positions in by_length.values():
            rows_x = np.concatenate(
                [np.repeat(windows[i].normalized[None], n_samples, 0) for i in positions]
            )
            rows_s = np.concatenate(
                [np.repeat(windows[i].stamps[None], n_samples, 0) for i in positions]
            )
            rows_y = np.concatenate([np.repeat(futures[i][None], n_samples, 0) for i in positions])

            outputs: list[np.ndarray[Any, np.dtype[np.float32]]] = []
            for start in range(0, len(rows_x), self.max_batch_rows):
                stop = start + self.max_batch_rows
                chunk = sample_paths(
                    self._tokenizer,
                    self._model,
                    torch.from_numpy(rows_x[start:stop]).to(device),
                    torch.from_numpy(rows_s[start:stop]).to(device),
                    torch.from_numpy(rows_y[start:stop]).to(device),
                    max_context=self.max_context,
                    pred_len=horizon,
                    clip=self.clip,
                    temperature=self.temperature,
                    top_k=self.top_k,
                    top_p=self.top_p,
                    generator=generator,
                    filter_fn=self._upstream.top_k_top_p_filtering,
                )
                outputs.append(chunk.cpu().numpy())
            merged = np.concatenate(outputs)
            for slot, position in enumerate(positions):
                decoded[position] = merged[slot * n_samples : (slot + 1) * n_samples]

        return [
            self._to_raw(windows[i], decoded[i], horizon, used_seed) for i in range(len(prepared))
        ]

    def _to_raw(
        self,
        window: ContextWindow,
        decoded: np.ndarray[Any, np.dtype[np.float32]],
        horizon: int,
        seed: int,
    ) -> RawForecast:
        values = window.denormalize(decoded)  # (n_samples, horizon, 6), real units
        extras = {
            name: np.ascontiguousarray(values[:, :, k])
            for k, name in enumerate(FEATURES)
            if k != CLOSE_INDEX
        }
        if self._device is None:  # pragma: no cover
            raise ModelError("Kronos failed to initialise")
        return RawForecast(
            samples=np.ascontiguousarray(values[:, :, CLOSE_INDEX]),
            extras=extras,
            model_version=self.spec.version if not self._injected else "injected",
            context_length_used=window.length,
            device=self._device,
            params={
                "temperature": self.temperature,
                "top_k": self.top_k,
                "top_p": self.top_p,
                "clip": self.clip,
                "max_context": self.max_context,
                "lookback": self.lookback,
                "max_batch_rows": self.max_batch_rows,
                "sampler_seed": seed,
                "upstream_commit": self._upstream.commit if self._upstream else None,
            },
            diagnostics={"context_truncated": window.truncated, "horizon": horizon},
        )
