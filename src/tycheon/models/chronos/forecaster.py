"""Chronos-2 (Amazon, Apache-2.0): marginal quantile forecasts, no sample paths.

Needs the ``chronos`` extra. Written against ``chronos-forecasting`` 2.3.2:
``Chronos2Pipeline.from_pretrained`` and ``predict_quantiles(inputs,
prediction_length, quantile_levels) -> (quantiles, mean)``, where each element of
``quantiles`` has shape ``(variates, horizon, levels)``.

Like TimesFM, Chronos-2 returns marginal quantiles per step, so ``samples`` is
``None`` and path-dependent risk measures must refuse its output. This adapter uses
only the univariate close series; Chronos-2 can also take covariates, which arrive
with the covariates work in a later phase.
"""

from __future__ import annotations

import importlib
from typing import Any

import numpy as np

from tycheon.errors import ModelError, OptionalDependencyError
from tycheon.models.base import BaseForecaster, PreparedHistory, RawForecast
from tycheon.models.devices import resolve_device

REPO_ID = "amazon/chronos-2"
#: Pinned Hugging Face commit; the weights cannot change underneath a forecast.
REVISION = "29ec3766d36d6f73f0696f85560a422f50e8498c"  # pragma: allowlist secret
DEFAULT_LEVELS: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)


class ChronosForecaster(BaseForecaster):
    """Zero-shot Chronos-2 forecasting the close price.

    Args:
        device: ``"auto"``, ``"cpu"``, ``"cuda"`` or ``"mps"``.
        quantile_levels: Levels to return, strictly inside (0, 1).
        max_context: Most recent bars given to the model.
        pipeline: An already-built pipeline (anything with ``predict_quantiles``), for
            tests and for callers who manage the model themselves.
    """

    model_id = "chronos-2"
    model_card = "docs/models/chronos-2.md"
    supports_paths = False
    min_history = 16

    def __init__(
        self,
        *,
        device: str = "auto",
        quantile_levels: tuple[float, ...] = DEFAULT_LEVELS,
        max_context: int = 2048,
        pipeline: Any | None = None,
        seed: int | None = 0,
    ) -> None:
        super().__init__(seed=seed)
        if not quantile_levels or any(not 0.0 < q < 1.0 for q in quantile_levels):
            raise ValueError("quantile_levels must lie strictly inside (0, 1)")
        if list(quantile_levels) != sorted(set(quantile_levels)):
            raise ValueError("quantile_levels must be strictly increasing")
        if max_context < self.min_history:
            raise ValueError(f"max_context must be at least {self.min_history}")
        self.device_request = device
        self.quantile_levels = tuple(quantile_levels)
        self.max_context = max_context
        self._pipeline = pipeline
        self._device = "injected" if pipeline is not None else None

    def load(self) -> ChronosForecaster:
        """Download (if needed) and initialise the model. Called lazily by ``predict``."""
        if self._pipeline is not None:
            return self
        try:
            chronos = importlib.import_module("chronos")
            torch = importlib.import_module("torch")
        except ImportError as exc:
            raise OptionalDependencyError(
                "Chronos is not installed. Install the extra: pip install 'tycheon[chronos]'"
            ) from exc
        device = resolve_device(self.device_request, torch)
        self._pipeline = chronos.Chronos2Pipeline.from_pretrained(
            REPO_ID, revision=REVISION, device_map=device
        )
        self._device = device
        return self

    def _forecast(
        self, prepared: PreparedHistory, horizon: int, n_samples: int, seed: int | None
    ) -> RawForecast:
        del n_samples, seed  # quantile model: no sampling
        self.load()
        if self._pipeline is None:  # pragma: no cover - load() always sets it
            raise ModelError("Chronos pipeline failed to initialise")

        close = prepared.frame["close"].to_numpy(dtype=np.float32)[-self.max_context :]
        quantiles, mean = self._pipeline.predict_quantiles(
            [close], prediction_length=horizon, quantile_levels=list(self.quantile_levels)
        )
        q = np.asarray(quantiles[0], dtype=np.float64)  # (variates, horizon, levels)
        m = np.asarray(mean[0], dtype=np.float64)
        if q.ndim != 3 or q.shape[0] != 1 or q.shape[1] != horizon:
            raise ModelError(f"unexpected Chronos quantile output shape {q.shape}")

        return RawForecast(
            samples=None,
            quantile_levels=self.quantile_levels,
            quantiles=np.ascontiguousarray(q[0].T),
            extras={"mean": m[0]},
            model_version=REVISION[:12],
            context_length_used=min(len(close), self.max_context),
            device=str(self._device),
            params={"max_context": self.max_context},
        )
