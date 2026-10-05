"""As-of stamped features from covariate series.

Every row of a feature matrix is computed *as of its own timestamp*: a row for time ``t``
uses only observations (and the versions of them) published at or before ``t``. A feature
built for an origin in 2021 therefore cannot see a macro revision published in 2022, a
filing made after the origin, or a news score published a minute later, which is exactly the
leakage that makes covariate models look better in backtests than they ever are live.

Three kinds of feature are provided:

* :class:`LatestValue`: the most recent observation known at ``t`` (a macro release, a
  fundamentals line item), how stale it is, and its change from the previous period.
* :class:`NewsSentiment`: aggregates of item-level sentiment *scores* over trailing windows.
  Only numbers are consumed; raw text is never read (it is untrusted data).
* :class:`FeatureBuilder`: runs a list of specs over many times at once.

Missing data is ``NaN``, not zero: "no filing yet" must not look like "a flat quarter".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import numpy as np
import pandas as pd

from tycheon.covariates.store import KEY, VALUE, CovariateStore
from tycheon.data.asof import AVAILABLE_AT, as_utc, to_ns
from tycheon.data.schema import TIMESTAMP
from tycheon.errors import LookaheadError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

_DAY_NS = 86_400 * 10**9


class FeatureSpec(Protocol):
    """One family of features from one covariate series."""

    series_id: str

    def compute(
        self, versions: pd.DataFrame, avail_ns: np.ndarray, t: pd.Timestamp
    ) -> dict[str, float]:
        """Features at time ``t`` from ``versions`` (sorted by ``available_at``)."""
        ...


def _latest_known(versions: pd.DataFrame, avail_ns: np.ndarray, t: pd.Timestamp) -> pd.DataFrame:
    """The latest version of each ``(timestamp, key)`` among those published by ``t``."""
    n = int(np.searchsorted(avail_ns, t.value, side="right"))
    prefix = versions.iloc[:n].reset_index()
    return prefix.drop_duplicates([TIMESTAMP, KEY], keep="last")


@dataclass(frozen=True)
class LatestValue:
    """Latest known value of a series, its age in days, and the change from the prior period."""

    series_id: str
    name: str

    def compute(
        self, versions: pd.DataFrame, avail_ns: np.ndarray, t: pd.Timestamp
    ) -> dict[str, float]:
        nan = float("nan")
        known = _latest_known(versions, avail_ns, t).sort_values(TIMESTAMP)
        if known.empty:
            return {self.name: nan, f"{self.name}_age_days": nan, f"{self.name}_change": nan}
        last = known.iloc[-1]
        change = float(last[VALUE] - known.iloc[-2][VALUE]) if len(known) > 1 else nan
        age = (t.value - pd.Timestamp(last[AVAILABLE_AT]).value) / _DAY_NS
        return {
            self.name: float(last[VALUE]),
            f"{self.name}_age_days": age,
            f"{self.name}_change": change,
        }


@dataclass(frozen=True)
class NewsSentiment:
    """Trailing aggregates of item-level sentiment scores in ``[-1, 1]``.

    For each window (in days): the mean score and the item count. Plus an exponentially
    decayed sum with the given half-life, so a story's weight fades smoothly.
    """

    series_id: str
    name: str
    windows_days: tuple[int, ...] = (1, 5)
    halflife_days: float = 2.0

    def compute(
        self, versions: pd.DataFrame, avail_ns: np.ndarray, t: pd.Timestamp
    ) -> dict[str, float]:
        known = _latest_known(versions, avail_ns, t)
        ts = known[TIMESTAMP].map(lambda x: pd.Timestamp(x).value).to_numpy(dtype=np.int64)
        age_days = (t.value - ts) / _DAY_NS
        values = known[VALUE].to_numpy(dtype=float)
        out: dict[str, float] = {}
        for w in self.windows_days:
            inside = (age_days >= 0) & (age_days < w)
            out[f"{self.name}_mean_{w}d"] = (
                float(values[inside].mean()) if inside.any() else float("nan")
            )
            out[f"{self.name}_n_{w}d"] = float(inside.sum())
        fresh = age_days >= 0
        decay = 0.5 ** (age_days[fresh] / self.halflife_days)
        out[f"{self.name}_ewm"] = float((values[fresh] * decay).sum())
        return out


class FeatureBuilder:
    """Builds a point-in-time feature matrix from a covariate store."""

    def __init__(self, store: CovariateStore, specs: Sequence[FeatureSpec]) -> None:
        if not specs:
            raise ValueError("at least one feature spec is required")
        names = [s.series_id for s in specs]
        self.store = store
        self.specs = list(specs)
        self._series = sorted(set(names))

    def build(self, times: pd.DatetimeIndex, *, as_of: datetime) -> pd.DataFrame:
        """One row per time in ``times``, each using only what was published by that time.

        ``as_of`` bounds what may be read at all (nothing published later is loaded); every
        time must be at or before it.
        """
        as_of_ts = as_utc(as_of)
        if len(times) and times.max() > as_of_ts:
            raise LookaheadError(
                f"feature time {times.max().isoformat()} is after as_of {as_of_ts.isoformat()}"
            )
        loaded = {}
        for series_id in self._series:
            versions = self.store.versions(series_id, as_of=as_of_ts)
            loaded[series_id] = (versions, to_ns(pd.DatetimeIndex(versions[AVAILABLE_AT])))
        rows = []
        for t in times:
            row: dict[str, float] = {}
            for spec in self.specs:
                versions, avail_ns = loaded[spec.series_id]
                row.update(spec.compute(versions, avail_ns, pd.Timestamp(t)))
            rows.append(row)
        return pd.DataFrame(rows, index=times)
