"""Fundamentals as bi-temporal numeric series, read as of a date.

A quarter's figures are known when they are *filed*, weeks after the quarter ends, and later
restated. Both are modelled the way the covariate store models them: ``timestamp`` is the
period, ``available_at`` is when that value could first be known, and a restatement is a new
row with a later ``available_at``. A review as of a date therefore sees the figure as it stood
then, not the restated one.

The bundled data is synthetic and deterministic. Real fundamentals come from the operator's own
licensed source loaded into the same store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from tycheon.covariates.store import CovariateStore
from tycheon.data.asof import as_utc
from tycheon.data.sample import SAMPLE_DATASETS
from tycheon.errors import DataValidationError
from tycheon.services.schemas import FundamentalsOut, FundamentalValue

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

METRICS = ("revenue_growth", "pe_ratio", "debt_to_equity")
FILING_LAG = pd.Timedelta(days=45)
RESTATEMENT_LAG = pd.Timedelta(days=110)


def series_id(symbol: str, metric: str) -> str:
    return f"{symbol}.{metric}"


class FundamentalsStore:
    """Fundamentals over a :class:`CovariateStore`."""

    def __init__(self, store: CovariateStore) -> None:
        self._store = store

    def snapshot(self, symbol: str, as_of: datetime) -> FundamentalsOut:
        """The latest figure of each metric known at ``as_of`` (missing metrics are omitted)."""
        when = as_utc(as_of)
        metrics: dict[str, FundamentalValue] = {}
        for metric in METRICS:
            value = self._latest(series_id(symbol, metric), when)
            if value is not None:
                metrics[metric] = value
        return FundamentalsOut(symbol=symbol, as_of=when.isoformat(), metrics=metrics)

    def _latest(self, series: str, when: datetime) -> FundamentalValue | None:
        """The latest figure known at ``when``, or ``None`` if the series does not exist."""
        try:
            frame = self._store.get(series, as_of=when)
        except DataValidationError:
            return None
        if frame.empty:
            return None
        row = frame.iloc[-1]
        return FundamentalValue(
            value=float(row["value"]),
            period_end=pd.Timestamp(frame.index[-1]).isoformat(),
            available_at=pd.Timestamp(row["available_at"]).isoformat(),
        )


def build_sample_fundamentals(root: Path) -> FundamentalsStore:
    """Write deterministic synthetic quarterly fundamentals for the sample symbols."""
    store = CovariateStore(root)
    quarters = pd.date_range("2018-03-31", "2023-09-30", freq="QE", tz="UTC")
    for s_index, symbol in enumerate(SAMPLE_DATASETS):
        rng = np.random.default_rng(1000 + s_index)
        base = {
            "revenue_growth": (0.06, 0.02),
            "pe_ratio": (18.0, 2.0),
            "debt_to_equity": (0.8, 0.1),
        }
        for metric, (mean, sd) in base.items():
            values = mean + sd * rng.standard_normal(len(quarters))
            frame = pd.DataFrame(
                {"value": values, "available_at": quarters + FILING_LAG},
                index=pd.DatetimeIndex(quarters, name="timestamp"),
            )
            store.put(series_id(symbol, metric), frame)
            # a restatement of the second-to-last quarter, published much later
            restated = frame.iloc[[-2]].copy()
            restated["value"] = restated["value"] * 1.08
            restated["available_at"] = restated.index + RESTATEMENT_LAG
            store.put(series_id(symbol, metric), restated)
    return FundamentalsStore(store)
