"""An append-only, bi-temporal store for non-price series: macro, fundamentals, news scores.

Same two-clock model as the bar store (see :mod:`tycheon.data.asof`): ``timestamp`` is the
period an observation describes (a month, a fiscal quarter, the minute a story ran) and
``available_at`` is when it could first have been known (a data release, a filing, the
publication time). The difference is where most covariate leakage lives:

* **Macro** series are revised. The value a series shows today for March is not the value that
  was known in April. A revision is a new row with a later ``available_at``.
* **Fundamentals** describe a quarter that ended long before the filing. Using the period end
  as the knowledge time leaks months.
* **News** arrives continuously; many items share a timestamp, so rows carry an optional
  ``key`` (an item id) that identifies one observation among simultaneous ones.

Only **numeric** values are stored. Tycheon does not ingest or interpret raw news text: it is
untrusted external data (AGENTS.md) and never follows instructions found in it. A sentiment
score arrives already computed, with the time it was published.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pandas as pd

from tycheon.data.asof import AVAILABLE_AT, as_utc, assert_available, require_not_future
from tycheon.data.schema import TIMESTAMP, validate_symbol
from tycheon.errors import DataValidationError

if TYPE_CHECKING:
    from datetime import datetime

KEY = "key"
VALUE = "value"
_COLUMNS = (KEY, VALUE, AVAILABLE_AT)
_QUOTE = chr(39)


def _quote(path: str) -> str:
    return _QUOTE + path.replace(_QUOTE, _QUOTE * 2) + _QUOTE


def _normalize(observations: pd.DataFrame) -> pd.DataFrame:
    """Canonical form: UTC ``timestamp`` index, ``key``, float ``value``, ``available_at``."""
    out = observations.copy()
    index = pd.DatetimeIndex(out.index)
    if index.tz is None:
        raise DataValidationError("covariate timestamps must be timezone-aware")
    out.index = index.tz_convert("UTC").rename(TIMESTAMP)
    missing = [c for c in (VALUE, AVAILABLE_AT) if c not in out.columns]
    if missing:
        raise DataValidationError(f"observations are missing {missing}; available_at is required")
    if KEY not in out.columns:
        out[KEY] = ""
    out[KEY] = out[KEY].astype(str)
    out[VALUE] = pd.to_numeric(out[VALUE], errors="raise").astype("float64")
    out[AVAILABLE_AT] = pd.to_datetime(out[AVAILABLE_AT], utc=True)
    if out[VALUE].isna().any() or not out[VALUE].map(pd.notna).all():
        raise DataValidationError("NaN in covariate values; leave missing observations out")
    if (pd.DatetimeIndex(out[AVAILABLE_AT]) < out.index).any():
        raise DataValidationError(
            "available_at before the observation's own timestamp: information from the future"
        )
    return out[list(_COLUMNS)].sort_index(kind="stable")


class CovariateStore:
    """Parquet-backed store of ``(series_id, timestamp, key) -> value`` with knowledge times."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def _dir(self, series_id: str) -> Path:
        validate_symbol(series_id)
        path = (self.root / "covariates" / f"series={series_id}").resolve()
        if self.root not in path.parents:
            raise DataValidationError(f"{series_id!r} resolves outside the store root")
        return path

    def series_ids(self) -> list[str]:
        base = self.root / "covariates"
        if not base.is_dir():
            return []
        return sorted(p.name.removeprefix("series=") for p in base.glob("series=*") if p.is_dir())

    def put(self, series_id: str, observations: pd.DataFrame) -> Path:
        """Append observations (``value``, ``available_at``, optional ``key``; time index)."""
        frame = _normalize(observations)
        directory = self._dir(series_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"part-{time.time_ns()}-{uuid.uuid4().hex[:8]}.parquet"
        frame.to_parquet(target, index=True)
        return target

    def _query(self, series_id: str, sql: str, params: list[Any]) -> pd.DataFrame:
        directory = self._dir(series_id)
        if not directory.is_dir() or not any(directory.glob("*.parquet")):
            raise DataValidationError(f"no covariate series {series_id!r}")
        glob = _quote((directory / "*.parquet").as_posix())
        con = duckdb.connect(":memory:")
        try:
            con.execute("SET TimeZone='UTC'")
            source = f"read_parquet({glob}, union_by_name=true)"
            frame: pd.DataFrame = (
                con.execute(sql.replace("{src}", source), params).to_arrow_table().to_pandas()
            )
            return frame
        finally:
            con.close()

    def get(
        self,
        series_id: str,
        *,
        as_of: datetime,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        """The series as known at ``as_of``: the latest version of each observation then.

        ``end``, if given, must not be after ``as_of``.
        """
        as_of_ts = as_utc(as_of)
        start_ts = as_utc(start, "start") if start is not None else None
        end_ts = as_utc(end, "end") if end is not None else None
        require_not_future(end_ts, as_of_ts)
        clauses = [f"{AVAILABLE_AT} <= CAST(? AS TIMESTAMPTZ)"]
        params: list[Any] = [as_of_ts.isoformat()]
        if start_ts is not None:
            clauses.append(f"{TIMESTAMP} >= CAST(? AS TIMESTAMPTZ)")
            params.append(start_ts.isoformat())
        if end_ts is not None:
            clauses.append(f"{TIMESTAMP} <= CAST(? AS TIMESTAMPTZ)")
            params.append(end_ts.isoformat())
        sql = (
            "SELECT * FROM {src} WHERE "  # noqa: S608
            + " AND ".join(clauses)
            + f" QUALIFY row_number() OVER (PARTITION BY {TIMESTAMP}, {KEY}"
            + f" ORDER BY {AVAILABLE_AT} DESC) = 1 ORDER BY {TIMESTAMP}, {KEY}"
        )
        frame = self._query(series_id, sql, params).set_index(TIMESTAMP)[list(_COLUMNS)]
        assert_available(frame, as_of_ts)
        return frame

    def versions(self, series_id: str, *, as_of: datetime) -> pd.DataFrame:
        """Every version of every observation published by ``as_of``, oldest publication first.

        This is what lets a feature builder ask "what did the series look like at time ``t``?"
        for many ``t <= as_of`` without a revision published later leaking into an earlier row.
        """
        as_of_ts = as_utc(as_of)
        sql = (
            f"SELECT * FROM {{src}} WHERE {AVAILABLE_AT} <= CAST(? AS TIMESTAMPTZ) "  # noqa: S608
            f"ORDER BY {AVAILABLE_AT}, {TIMESTAMP}, {KEY}"
        )
        frame = self._query(series_id, sql, [as_of_ts.isoformat()]).set_index(TIMESTAMP)[
            list(_COLUMNS)
        ]
        assert_available(frame, as_of_ts)
        return frame
