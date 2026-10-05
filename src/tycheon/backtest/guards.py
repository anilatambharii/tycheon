"""Hard leakage guards for backtests, and the survivorship warning.

A backtest with lookahead in it is worse than none: it reports confident, plausible,
wrong numbers. The guards here make the wrong thing raise instead of silently inflating a
score:

* every input a forecaster sees must have been published by the origin
  (``available_at <= origin``; a bar known at the origin has ``available_at == origin``, so
  a strict ``<`` would discard the newest bar the forecaster is entitled to);
* the data used to *fit* anything (a router, a calibrator) must end before the first
  origin it serves, by at least the embargo;
* outcomes are always strictly after the origin.

:class:`GuardedForecaster` enforces the first rule on every ``predict`` call and counts the
checks, so a result can state how many were made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

from tycheon.data.asof import AVAILABLE_AT, as_utc
from tycheon.errors import LookaheadError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from tycheon.models.base import ForecastDistribution, Forecaster


def latest_available(frame: pd.DataFrame) -> pd.Timestamp:
    """The latest publication time in ``frame`` (UTC)."""
    if len(frame) == 0:
        return pd.Timestamp.min.tz_localize("UTC")
    return pd.Timestamp(pd.to_datetime(frame[AVAILABLE_AT], utc=True).max())


def assert_known_by(frame: pd.DataFrame, origin: datetime, what: str = "input") -> None:
    """Raise :class:`LookaheadError` unless every row of ``frame`` was published by ``origin``."""
    origin_ts = as_utc(origin, "origin")
    latest = latest_available(frame)
    if latest > origin_ts:
        raise LookaheadError(
            f"{what} contains data published at {latest.isoformat()}, after the origin "
            f"{origin_ts.isoformat()}"
        )


def assert_fit_data_precedes(
    frame: pd.DataFrame, first_origin: datetime, embargo_bars: int, bars: pd.DataFrame
) -> None:
    """Raise unless ``frame`` ends at least ``embargo_bars`` bars before ``first_origin``.

    ``bars`` is the full series, used to count bars. The fitting data's last row must lie
    ``embargo_bars`` or more bars before the bar that is the first origin.
    """
    if len(frame) == 0:
        return
    origin_ts = as_utc(first_origin, "first_origin")
    available = pd.DatetimeIndex(pd.to_datetime(bars[AVAILABLE_AT], utc=True))
    origin_pos = int(available.searchsorted(origin_ts, side="right")) - 1
    last_fit = latest_available(frame)
    fit_pos = int(available.searchsorted(last_fit, side="right")) - 1
    if origin_pos - fit_pos < embargo_bars:
        raise LookaheadError(
            f"fit data ends {origin_pos - fit_pos} bars before the first origin; the embargo "
            f"requires at least {embargo_bars}"
        )


@dataclass
class GuardedForecaster:
    """Wrap a forecaster so every ``predict`` call is checked for lookahead.

    The wrapper checks that the history passed in was published by ``as_of`` *and* that
    ``as_of`` is the origin the walk-forward engine assigned (so a forecaster cannot be
    handed a later ``as_of`` than the origin it is being scored at).
    """

    inner: Forecaster
    expected_origin: pd.Timestamp | None = None
    checks: int = field(default=0)

    @property
    def model_id(self) -> str:
        return self.inner.model_id

    @property
    def model_card(self) -> str:
        return self.inner.model_card

    @property
    def supports_paths(self) -> bool:
        return self.inner.supports_paths

    def fit(self, history: pd.DataFrame | None = None) -> GuardedForecaster:
        if history is not None and self.expected_origin is not None:
            assert_known_by(history, self.expected_origin, "fit history")
        self.inner.fit(history)
        return self

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
        if self.expected_origin is not None and as_of_ts != self.expected_origin:
            raise LookaheadError(
                f"predict was called with as_of {as_of_ts.isoformat()}, but the origin being "
                f"scored is {self.expected_origin.isoformat()}"
            )
        assert_known_by(history, as_of_ts, "history")
        self.checks += 1
        return self.inner.predict(history, horizon, n_samples, as_of, seed=seed)


def survivorship_warning(
    universe: Sequence[str], *, universe_kind: str, synthetic: bool
) -> str | None:
    """A warning for a static universe; ``None`` when point-in-time or synthetic.

    A fixed list of symbols chosen today contains only names that survived to today. Back-tests
    on it flatter every model, because delisted failures are missing.
    """
    if universe_kind == "point_in_time":
        return None
    if synthetic:
        return None
    return (
        f"survivorship bias: the universe of {len(universe)} symbols is static (chosen with "
        "hindsight), so delisted and failed names are absent and every model's results are "
        "flattered. Use a point-in-time universe before drawing conclusions."
    )
