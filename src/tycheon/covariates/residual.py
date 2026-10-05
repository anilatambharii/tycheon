"""Residual correction: a base forecaster plus a model of what covariates say it gets wrong.

The base forecaster (any :class:`~tycheon.models.base.Forecaster`) predicts from price history
alone. This module asks whether macro, fundamentals or news explain the part it misses: it
replays the base forecaster at past origins, records the *residual* (outcome minus the base
median, in log-return space), and fits a model of that residual on features that were known at
each origin. The prediction shifts the base distribution by the predicted residual.

The interface is :class:`ResidualCorrector`, so a better fusion method can replace the
default :class:`LightGBMResidualCorrector` without touching anything else.

Two safeguards keep this honest. The correction is only applied if it **beats a zero
correction on a held-out, chronologically later block** by a margin; otherwise the base
forecast passes through unchanged and the report says so ("publish when the baseline wins").
And a corrected forecast is **never allowed to be made as of a time earlier than the data the
corrector was trained on**.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol

import lightgbm as lgb
import numpy as np
import pandas as pd
from numpy.typing import NDArray

from tycheon.calibration.scores import collect_scores
from tycheon.data.asof import AVAILABLE_AT, as_utc
from tycheon.errors import LookaheadError, ModelError
from tycheon.models.base import BaseForecaster, ForecastDistribution, RawForecast

if TYPE_CHECKING:
    from datetime import datetime

    from tycheon.covariates.features import FeatureBuilder
    from tycheon.models.base import Forecaster, PreparedHistory

Floats = NDArray[np.float64]


@dataclass(frozen=True)
class CorrectorReport:
    """How a residual corrector did on a held-out block, and whether it will be used."""

    n_train: int
    n_valid: int
    n_test: int
    mse_zero: float
    mse_model: float
    improvement: float  #: ``1 - mse_model / mse_zero``; positive means better than no correction
    accepted: bool
    reason: str
    best_iteration: int = 0
    importance: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_train": self.n_train,
            "n_valid": self.n_valid,
            "n_test": self.n_test,
            "mse_zero_correction": self.mse_zero,
            "mse_corrected": self.mse_model,
            "improvement": self.improvement,
            "accepted": self.accepted,
            "reason": self.reason,
            "best_iteration": self.best_iteration,
            "feature_importance": self.importance,
        }


class ResidualCorrector(Protocol):
    """Fit residuals from point-in-time features; predict a per-step correction."""

    def fit(self, features: pd.DataFrame, residuals: Floats) -> CorrectorReport:
        """``features`` has one row per origin; ``residuals`` is ``(n, horizon)``."""
        ...

    def predict(self, features: pd.DataFrame) -> Floats:
        """``(rows, horizon)`` predicted residual (log-return units)."""
        ...


def _stack_x(features: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """One block of rows per horizon step, with the step as an extra feature."""
    blocks = []
    for h in range(horizon):
        block = features.copy()
        block["__step__"] = float(h + 1)
        blocks.append(block)
    return pd.concat(blocks, ignore_index=True)


def _stack_y(residuals: Floats) -> Floats:
    return np.concatenate([residuals[:, h] for h in range(residuals.shape[1])])


class LightGBMResidualCorrector:
    """A small, heavily regularised LightGBM model shared across horizon steps.

    Origins are split chronologically into train, validation (early stopping) and a final test
    block that decides whether the correction is accepted. Financial residuals are mostly
    noise, so the defaults are deliberately conservative: shallow trees, strong L2, a high
    minimum leaf size, and a required improvement (``min_improvement``) over predicting zero.
    """

    def __init__(
        self,
        *,
        min_origins: int = 80,
        min_improvement: float = 0.02,
        train_fraction: float = 0.6,
        valid_fraction: float = 0.2,
        seed: int = 0,
    ) -> None:
        if (
            not 0 < train_fraction < 1
            or not 0 < valid_fraction < 1
            or train_fraction + valid_fraction >= 1
        ):
            raise ValueError("fractions must leave room for a test block")
        self.min_origins = min_origins
        self.min_improvement = min_improvement
        self.train_fraction = train_fraction
        self.valid_fraction = valid_fraction
        self.seed = seed
        self._model: Any = None
        self._columns: list[str] = []
        self._scale = 1.0
        self.horizon = 0
        self.report: CorrectorReport | None = None

    def _params(self) -> dict[str, Any]:
        """LightGBM native parameters (no scikit-learn needed). Shallow, regularised, seeded."""
        return {
            "objective": "regression",
            "learning_rate": 0.03,
            "num_leaves": 7,
            "max_depth": 3,
            "min_data_in_leaf": 40,
            "lambda_l2": 10.0,
            "bagging_fraction": 0.8,
            "bagging_freq": 1,
            "feature_fraction": 0.8,
            "seed": self.seed,
            "deterministic": True,
            "force_row_wise": True,
            "num_threads": 1,
            "verbosity": -1,
        }

    def fit(self, features: pd.DataFrame, residuals: Floats) -> CorrectorReport:
        n, horizon = residuals.shape
        self.horizon = horizon
        self._columns = list(features.columns)
        if n < self.min_origins:
            self.report = CorrectorReport(
                n, 0, 0, float("nan"), float("nan"), 0.0, False,
                f"only {n} origins (need {self.min_origins})",
            )  # fmt: skip
            return self.report

        self._scale = float(np.std(residuals)) or 1.0
        y = residuals / self._scale
        n_train, n_valid = int(self.train_fraction * n), int(self.valid_fraction * n)
        tr = slice(0, n_train)
        va = slice(n_train, n_train + n_valid)
        te = slice(n_train + n_valid, n)
        x_tr, y_tr = _stack_x(features.iloc[tr], horizon), _stack_y(y[tr])
        x_va, y_va = _stack_x(features.iloc[va], horizon), _stack_y(y[va])
        x_te, y_te = _stack_x(features.iloc[te], horizon), _stack_y(y[te])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            train_set = lgb.Dataset(x_tr, label=y_tr)
            valid_set = lgb.Dataset(x_va, label=y_va, reference=train_set)
            probe = lgb.train(
                self._params(),
                train_set,
                num_boost_round=400,
                valid_sets=[valid_set],
                callbacks=[lgb.early_stopping(25, verbose=False)],
            )
            best = max(1, int(probe.best_iteration))
            pred = probe.predict(x_te, num_iteration=best)
        mse_zero = float(np.mean(y_te**2))
        mse_model = float(np.mean((y_te - pred) ** 2))
        improvement = 1.0 - mse_model / mse_zero if mse_zero > 0 else 0.0
        accepted = improvement >= self.min_improvement
        n_test = n - n_train - n_valid

        importance: dict[str, float] = {}
        if accepted:
            keep = slice(0, n_train + n_valid)
            x_all, y_all = _stack_x(features.iloc[keep], horizon), _stack_y(y[keep])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                final = lgb.train(
                    self._params(), lgb.Dataset(x_all, label=y_all), num_boost_round=best
                )
            self._model = final
            gains = final.feature_importance("gain")
            total = float(gains.sum()) or 1.0
            names = final.feature_name()
            importance = {
                name: float(g) / total
                for name, g in zip(names, gains, strict=True)
                if name != "__step__"
            }
            reason = f"beat a zero correction on {n_test} held-out origins by {improvement:.1%}"
        else:
            self._model = None
            reason = (
                f"did not beat a zero correction on the held-out block (improvement "
                f"{improvement:.1%}, needed {self.min_improvement:.0%}); the base forecast is "
                "passed through unchanged"
            )
        scale2 = self._scale**2
        self.report = CorrectorReport(
            n_train, n_valid, n_test, mse_zero * scale2, mse_model * scale2, improvement,
            accepted, reason, best, importance,
        )  # fmt: skip
        return self.report

    def predict(self, features: pd.DataFrame) -> Floats:
        if self._model is None:
            return np.zeros((len(features), max(self.horizon, 1)))
        stacked = _stack_x(features[self._columns], self.horizon)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            raw = self._model.predict(stacked)
        shaped = np.asarray(raw, dtype=np.float64).reshape(self.horizon, len(features)).T
        return np.asarray(shaped * self._scale, dtype=np.float64)


class ResidualCorrectedForecaster(BaseForecaster):
    """Shift a base forecaster's distribution by a covariate-driven residual prediction.

    Call :meth:`train` (or ``fit``) with bars known at some ``as_of`` first. Until then, or if
    the corrector was not accepted, forecasts equal the base forecaster's.
    """

    model_card = "docs/models/residual-corrected.md"
    min_history = 2

    def __init__(
        self,
        base: Forecaster,
        builder: FeatureBuilder,
        corrector: ResidualCorrector | None = None,
        *,
        horizon: int,
        n_origins: int = 150,
        stride: int | None = None,
        n_samples: int = 30,
        max_history: int | None = None,
        seed: int | None = 0,
    ) -> None:
        super().__init__(seed=seed)
        self.base = base
        self.builder = builder
        self.corrector: ResidualCorrector = corrector or LightGBMResidualCorrector(seed=seed or 0)
        self.horizon = horizon
        self.n_origins = n_origins
        self.stride = stride
        self.n_samples = n_samples
        self.max_history = max_history
        self.model_id = f"residual({base.model_id})"
        self.supports_paths = base.supports_paths
        self.report: CorrectorReport | None = None
        self._trained_as_of: pd.Timestamp | None = None

    def train(self, bars: pd.DataFrame, *, as_of: datetime) -> CorrectorReport:
        """Replay the base forecaster, build point-in-time features, fit the corrector."""
        as_of_ts = as_utc(as_of)
        scores = collect_scores(
            self.base,
            bars,
            as_of=as_of_ts,
            horizon=self.horizon,
            n_origins=self.n_origins,
            stride=self.stride,
            n_samples=self.n_samples,
            levels=(0.5,),
            max_history=self.max_history,
        )
        residuals = scores.realized - scores.base_logq[:, 0, :]
        features = self.builder.build(scores.origin_times, as_of=as_of_ts)
        self.report = self.corrector.fit(features, residuals)
        self._trained_as_of = max(as_of_ts, scores.max_outcome_time)
        return self.report

    def fit(self, history: pd.DataFrame | None = None) -> ResidualCorrectedForecaster:
        if history is None or AVAILABLE_AT not in history.columns:
            raise ModelError(
                "fit needs bars with an available_at column; or call train(bars, as_of=...)"
            )
        self.train(history, as_of=pd.Timestamp(history[AVAILABLE_AT].max()))
        return self

    def _forecast(
        self, prepared: PreparedHistory, horizon: int, n_samples: int, seed: int | None
    ) -> RawForecast:  # pragma: no cover - predict() is overridden
        raise NotImplementedError

    def predict(
        self,
        history: pd.DataFrame,
        horizon: int,
        n_samples: int,
        as_of: datetime,
        *,
        seed: int | None = None,
    ) -> ForecastDistribution:
        as_of_ts = as_utc(as_of)
        if self._trained_as_of is not None and self._trained_as_of > as_of_ts:
            raise LookaheadError(
                f"the corrector was trained on data known at {self._trained_as_of.isoformat()}, "
                f"after this forecast's as_of {as_of_ts.isoformat()}"
            )
        if horizon > self.horizon:
            raise ModelError(f"horizon {horizon} exceeds the trained horizon {self.horizon}")
        base = self.base.predict(history, horizon, n_samples, as_of_ts, seed=seed)

        accepted = self.report is not None and self.report.accepted
        if accepted:
            features = self.builder.build(pd.DatetimeIndex([as_of_ts]), as_of=as_of_ts)
            shift = np.asarray(self.corrector.predict(features), dtype=np.float64)[0, :horizon]
        else:
            shift = np.zeros(horizon)
        factor = np.exp(shift)
        diagnostics = {
            **base.metadata.diagnostics,
            "residual_correction_applied": bool(accepted),
            "residual_correction_mean_abs": float(np.abs(shift).mean()),
            "residual_corrector": None if self.report is None else self.report.reason,
        }
        return replace(
            base,
            quantiles=base.quantiles * factor[None, :],
            samples=None if base.samples is None else base.samples * factor[None, :],
            model_mix={self.model_id: 1.0},
            model_card=self.model_card,
            calibration_status="uncalibrated",
            calibration=None,
            metadata=replace(base.metadata, model_id=self.model_id, diagnostics=diagnostics),
        )
