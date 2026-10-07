"""Fine-tuning datasets: validation, the walk-forward split, and the leakage checks.

The tenant's evaluation is a walk-forward test region at the end of each series (the same layout
the OSS backtester uses). Everything the model is trained on must end *before* that region, with an
embargo, so a candidate is never scored on bars it learned from. :func:`assert_no_test_leakage`
re-checks that on the finished windows, independently of the code that built them.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from tycheon.backtest.walk_forward import WalkForwardConfig, plan_origins
from tycheon.models.kronos.windowing import calendar_features, feature_matrix

if TYPE_CHECKING:
    from numpy.typing import NDArray

MIN_TRAIN_WINDOWS = 24
MAX_WINDOWS = 4096
CLIP = 5.0
_EPS = 1e-5


class DatasetError(ValueError):
    """The data cannot be used to fine-tune. The message is safe to show the customer."""


class LeakageError(DatasetError):
    """A training window reaches into the evaluation region. This is a bug, never a user error."""


class FineTuneConfig(BaseModel):
    """What a customer may choose. Everything is bounded: it comes from an HTTP request."""

    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(min_length=1, max_length=10)
    horizon: int = Field(default=5, ge=1, le=30)
    lookback: int = Field(default=96, ge=32, le=400)
    epochs: int = Field(default=2, ge=1, le=20)
    batch_size: int = Field(default=16, ge=2, le=64)
    learning_rate: float = Field(default=5e-5, gt=0, le=1e-3)
    max_steps: int = Field(default=200, ge=1, le=5000)
    test_window: int = Field(default=40, ge=20, le=120)
    folds: int = Field(default=1, ge=1, le=3)
    eval_samples: int = Field(default=50, ge=20, le=200)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)


@dataclass(frozen=True)
class SeriesSplit:
    """Where one series is cut: training ends before the first test origin, minus the embargo."""

    symbol: str
    n_bars: int
    first_test_origin: int
    train_end: int  # exclusive: bars [0, train_end) may be trained on
    val_start: int  # validation windows begin here (inside the training region)


@dataclass(frozen=True)
class WindowSet:
    """Normalised training windows with the bar index each one ends at (for the leakage check)."""

    x: NDArray[np.float32]  # [n, lookback + horizon, 6]
    stamps: NDArray[np.float32]  # [n, lookback + horizon, 5]
    last_bar: NDArray[np.int64]  # [n] index (in its own series) of each window's final bar
    series: list[str]  # [n] which symbol each window came from

    def __len__(self) -> int:
        return len(self.x)


@dataclass
class DatasetReport:
    n_series: int
    n_train_windows: int
    n_val_windows: int
    warnings: list[str] = field(default_factory=list)


def split_for(symbol: str, n_bars: int, config: FineTuneConfig) -> SeriesSplit:
    """The train/test cut, taken from the OSS walk-forward planner so they cannot disagree."""
    wf = WalkForwardConfig(
        horizon=config.horizon,
        folds=config.folds,
        test_window=config.test_window,
        embargo=config.horizon,
    )
    origins, _ = plan_origins(n_bars, wf)
    first = origins[0]
    train_end = first - wf.embargo
    needed = config.lookback + config.horizon + MIN_TRAIN_WINDOWS
    if train_end < needed:
        raise DatasetError(
            f"{symbol}: {n_bars} bars is too few. Fine-tuning needs at least {needed} bars before "
            f"the {config.folds * config.test_window}-bar evaluation region; this series has "
            f"{max(train_end, 0)}."
        )
    # a validation region at the end of the training region: long enough for a few dozen windows,
    # never more than a quarter of the data, and absent altogether if there is no room for one
    size = config.lookback + config.horizon
    val_len = min(size + 24, train_end // 4)
    val_start = train_end - val_len if val_len >= size + 2 else train_end
    return SeriesSplit(symbol, n_bars, first, train_end, val_start)


def validate_bars(symbol: str, bars: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Reject data a model must not learn from; return it unchanged if it is sound."""
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise DatasetError(f"{symbol}: timestamps must be timezone-aware")
    if not bars.index.is_monotonic_increasing or bars.index.has_duplicates:
        raise DatasetError(f"{symbol}: timestamps must be strictly increasing and unique")
    for column in ("open", "high", "low", "close"):
        if column not in bars.columns:
            raise DatasetError(f"{symbol}: missing the {column} column")
    matrix = feature_matrix(bars)  # raises on NaN or infinity
    if (matrix[:, :4] <= 0).any():
        raise DatasetError(f"{symbol}: prices must be positive")
    if (bars["high"] < bars[["open", "close", "low"]].max(axis=1) - 1e-9).any():
        raise DatasetError(f"{symbol}: some bars have a high below their open, close or low")
    if "available_at" in bars.columns:
        published = pd.DatetimeIndex(bars["available_at"])
        if (published < bars.index).any():
            raise DatasetError(
                f"{symbol}: some bars are marked available before they happened (look-ahead)"
            )
        if (published > as_of).any():
            raise DatasetError(f"{symbol}: the data contains bars published after {as_of}")
    elif bars.index[-1] > as_of:
        raise DatasetError(f"{symbol}: the data contains bars after {as_of}")
    return bars


def reject_duplicate_series(series: dict[str, pd.DataFrame]) -> None:
    """The same data under two names would put a test series into training under another symbol."""
    seen: dict[str, str] = {}
    for symbol, bars in series.items():
        digest = hashlib.sha256(
            np.round(bars["close"].to_numpy(dtype="float64"), 6).tobytes()
        ).hexdigest()
        if digest in seen:
            raise DatasetError(
                f"{symbol} and {seen[digest]} have identical prices: duplicated data would leak "
                "evaluation bars into training"
            )
        seen[digest] = symbol


def _windows_for(
    bars: pd.DataFrame, lo: int, hi: int, config: FineTuneConfig
) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.int64]]:
    """Windows lying entirely inside bars[lo:hi], each normalised by its own *context* only."""
    size = config.lookback + config.horizon
    matrix = feature_matrix(bars)
    stamps = calendar_features(pd.DatetimeIndex(bars.index))
    xs, ss, ends = [], [], []
    for start in range(lo, hi - size + 1):
        window = matrix[start : start + size]
        context = window[: config.lookback]  # statistics never see the horizon, as at inference
        mean, std = context.mean(axis=0), context.std(axis=0)
        xs.append(np.clip((window - mean) / (std + _EPS), -CLIP, CLIP).astype(np.float32))
        ss.append(stamps[start : start + size])
        ends.append(start + size - 1)
    if not xs:
        shape = (0, size)
        return (
            np.zeros((*shape, 6), np.float32),
            np.zeros((*shape, 5), np.float32),
            np.zeros(0, np.int64),
        )
    return np.stack(xs), np.stack(ss), np.asarray(ends, dtype=np.int64)


def build_windows(
    series: dict[str, pd.DataFrame], config: FineTuneConfig
) -> tuple[WindowSet, WindowSet, dict[str, SeriesSplit], DatasetReport]:
    """Training and validation windows for every series, built strictly before the test region."""
    reject_duplicate_series(series)
    rng = np.random.default_rng(config.seed)
    splits: dict[str, SeriesSplit] = {}
    parts: dict[str, list[tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.int64], str]]]
    parts = {"train": [], "val": []}
    for symbol, bars in series.items():
        split = splits[symbol] = split_for(symbol, len(bars), config)
        # training windows end a full horizon before validation begins (an embargo between them)
        train_hi = (
            split.val_start - config.horizon
            if split.val_start < split.train_end
            else split.train_end
        )
        val_lo = split.val_start
        for name, lo, hi in (("train", 0, train_hi), ("val", val_lo, split.train_end)):
            x, s, e = _windows_for(bars, lo, hi, config)
            parts[name].append((x, s, e, symbol))
    sets = []
    for name in ("train", "val"):
        xs = [p[0] for p in parts[name] if len(p[0])]
        if not xs:
            sets.append(
                WindowSet(
                    np.zeros((0, 1, 6), np.float32),
                    np.zeros((0, 1, 5), np.float32),
                    np.zeros(0, np.int64),
                    [],
                )
            )
            continue
        x = np.concatenate(xs)
        st = np.concatenate([p[1] for p in parts[name] if len(p[1])])
        ends = np.concatenate([p[2] for p in parts[name] if len(p[2])])
        names = [p[3] for p in parts[name] for _ in range(len(p[0]))]
        sets.append(WindowSet(x, st, ends, names))
    train, val = sets
    if len(train) > MAX_WINDOWS:  # a fixed-seed subsample keeps cost bounded and reproducible
        pick = np.sort(rng.choice(len(train), MAX_WINDOWS, replace=False))
        train = WindowSet(
            train.x[pick], train.stamps[pick], train.last_bar[pick], [train.series[i] for i in pick]
        )
    assert_no_test_leakage(train, splits)
    assert_no_test_leakage(val, splits)
    if len(train) < MIN_TRAIN_WINDOWS:
        raise DatasetError(f"only {len(train)} training windows: add more history")
    report = DatasetReport(len(series), len(train), len(val))
    if len(val) == 0:
        report.warnings.append("no validation windows: the loss history is training loss only")
    return train, val, splits, report


def assert_no_test_leakage(windows: WindowSet, splits: dict[str, SeriesSplit]) -> None:
    """Every window must end before its series' evaluation region (minus the embargo)."""
    for last, symbol in zip(windows.last_bar.tolist(), windows.series, strict=True):
        if last >= splits[symbol].train_end:
            raise LeakageError(
                f"{symbol}: a training window ends at bar {last}, inside the evaluation region "
                f"that starts at {splits[symbol].train_end}"
            )
