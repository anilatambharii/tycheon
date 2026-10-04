"""TimesFM 2.5 (Google, Apache-2.0): marginal quantile forecasts, no sample paths.

Needs the ``timesfm`` extra. Written against the PyPI ``timesfm`` 3.0.2 API:
``TimesFM_2p5_200M_torch.from_pretrained``, ``compile(ForecastConfig(...))``, and
``forecast(horizon, inputs) -> (point, quantiles)`` where ``quantiles`` has shape
``(series, horizon, 10)``: channel 0 is the mean and channels 1..9 are the 0.1..0.9
quantiles.

TimesFM emits *marginal* distributions per step. It cannot say how step ``h`` and step
``h + 1`` move together, so this forecaster returns ``samples=None`` and anything that
needs a joint path (drawdown probability, horizon expected shortfall) must refuse it
rather than invent a correlation structure.

TimesFM chooses its own device (CUDA if present, else CPU) and exposes no switch, so
this class has no ``device`` argument; the device it picked is recorded in the metadata.
"""

from __future__ import annotations

import importlib
from typing import Any

import numpy as np

from tycheon.errors import ModelError, OptionalDependencyError
from tycheon.models.base import BaseForecaster, PreparedHistory, RawForecast

REPO_ID = "google/timesfm-2.5-200m-pytorch"
#: Pinned Hugging Face commit; the weights cannot change underneath a forecast.
REVISION = "1d952420fba87f3c6dee4f240de0f1a0fbc790e3"  # pragma: allowlist secret
#: Channel 0 of the quantile output is the mean; 1..9 are these levels.
NATIVE_LEVELS: tuple[float, ...] = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


class TimesFMForecaster(BaseForecaster):
    """Zero-shot TimesFM 2.5 (200M) forecasting the close price.

    Args:
        max_context: Most recent bars given to the model (rounded up by TimesFM to a
            multiple of its 32-bar patch). Upstream supports up to 16384.
        max_horizon: Longest horizon the compiled model serves (rounded up to a
            multiple of 128). A longer request raises rather than silently recompiling.
        engine: An already-compiled TimesFM object (anything with ``forecast``), for
            tests and for callers who manage the model themselves.
    """

    model_id = "timesfm-2.5-200m"
    model_card = "docs/models/timesfm.md"
    supports_paths = False
    min_history = 16

    def __init__(
        self,
        *,
        max_context: int = 1024,
        max_horizon: int = 128,
        engine: Any | None = None,
        seed: int | None = 0,
    ) -> None:
        super().__init__(seed=seed)
        if max_context < self.min_history or max_horizon < 1:
            raise ValueError("max_context and max_horizon must be positive and sensible")
        self.max_context = max_context
        self.max_horizon = max_horizon
        self._engine = engine

    def load(self) -> TimesFMForecaster:
        """Download (if needed) and compile the model. Called lazily by ``predict``."""
        if self._engine is not None:
            return self
        try:
            timesfm = importlib.import_module("timesfm")
        except ImportError as exc:
            raise OptionalDependencyError(
                "TimesFM is not installed. Install the extra: pip install 'tycheon[timesfm]'"
            ) from exc
        model_cls = getattr(timesfm, "TimesFM_2p5_200M_torch", None)
        if model_cls is None:
            raise OptionalDependencyError(
                "this timesfm install has no PyTorch backend; install 'tycheon[timesfm]' "
                "(timesfm[torch])"
            )
        # torch_compile=False: compiling needs a working C++ toolchain and buys nothing
        # for the short, one-off calls Tycheon makes.
        engine = model_cls.from_pretrained(REPO_ID, revision=REVISION, torch_compile=False)
        engine.compile(
            timesfm.ForecastConfig(
                max_context=self.max_context,
                max_horizon=self.max_horizon,
                normalize_inputs=True,
                use_continuous_quantile_head=True,
                force_flip_invariance=True,
                infer_is_positive=True,  # prices are positive
                fix_quantile_crossing=True,
            )
        )
        self._engine = engine
        return self

    def _forecast(
        self, prepared: PreparedHistory, horizon: int, n_samples: int, seed: int | None
    ) -> RawForecast:
        del n_samples, seed  # deterministic quantile model: no sampling
        if horizon > self.max_horizon:
            raise ModelError(
                f"horizon {horizon} exceeds max_horizon={self.max_horizon}; construct "
                "TimesFMForecaster(max_horizon=...) with a larger value"
            )
        self.load()
        if self._engine is None:  # pragma: no cover - load() always sets it
            raise ModelError("TimesFM engine failed to initialise")

        close = prepared.frame["close"].to_numpy(dtype=np.float64)[-self.max_context :]
        point, quantiles = self._engine.forecast(horizon=horizon, inputs=[close])
        point_arr = np.asarray(point, dtype=np.float64)[0, :horizon]
        quant_arr = np.asarray(quantiles, dtype=np.float64)[0, :horizon, :]
        if quant_arr.shape[-1] != len(NATIVE_LEVELS) + 1:
            raise ModelError(
                f"unexpected TimesFM quantile output {quant_arr.shape}; expected "
                f"{len(NATIVE_LEVELS) + 1} channels (mean + {len(NATIVE_LEVELS)} quantiles)"
            )

        device = getattr(getattr(self._engine, "model", None), "device", "cpu")
        return RawForecast(
            samples=None,
            quantile_levels=NATIVE_LEVELS,
            quantiles=np.ascontiguousarray(quant_arr[:, 1:].T),
            extras={"mean": point_arr},
            model_version=REVISION[:12],
            context_length_used=min(len(close), self.max_context),
            device=str(device),
            params={"max_context": self.max_context, "max_horizon": self.max_horizon},
        )
