"""CSV and Parquet files on disk: the provider for data you already licensed."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd

from tycheon.data import actions as actions_mod
from tycheon.data.providers.base import ProviderBase
from tycheon.data.schema import NO_LAG, TIMESTAMP, validate_symbol
from tycheon.errors import ProviderError

# Column spellings seen in the wild, including the example CSVs shipped with
# Kronos (``timestamps``). Matching is case-insensitive.
_TIMESTAMP_ALIASES = ("timestamp", "timestamps", "datetime", "date", "time")


class FileProvider(ProviderBase):
    """Reads ``<root>/<symbol>.csv`` or ``.parquet`` (and ``<symbol>.actions.<ext>``).

    The files are *yours*: Tycheon neither ships nor redistributes market data, so
    this provider only ever reads what the operator pointed it at. Files without an
    ``available_at`` column get the conservative default of ``timestamp + bar
    duration`` (see :mod:`tycheon.data.asof`).
    """

    name = "file"
    license_terms = "Your own data under your own licence. Tycheon does not redistribute it."

    def __init__(
        self,
        root: str | Path,
        *,
        fmt: Literal["csv", "parquet"] = "csv",
        source_tz: str = "UTC",
        availability_lag: pd.Timedelta = NO_LAG,
    ) -> None:
        if fmt not in ("csv", "parquet"):
            raise ProviderError(f"unsupported file format {fmt!r}")
        self.root = Path(root).resolve()
        self.fmt = fmt
        self.source_tz = source_tz
        self.availability_lag = availability_lag

    def _path(self, symbol: str, suffix: str = "") -> Path:
        validate_symbol(symbol)
        path = (self.root / f"{symbol}{suffix}.{self.fmt}").resolve()
        # validate_symbol already forbids separators; this is the belt to its braces.
        if self.root not in path.parents:
            raise ProviderError(f"{symbol!r} resolves outside the data root")
        return path

    def _read(self, path: Path) -> pd.DataFrame:
        if not path.is_file():
            raise ProviderError(f"no data file for this symbol at {path.name}")
        return pd.read_csv(path) if self.fmt == "csv" else pd.read_parquet(path)

    @staticmethod
    def _index_by_time(frame: pd.DataFrame, index_name: str) -> pd.DataFrame:
        lowered = {str(c).lower(): c for c in frame.columns}
        # An actions file is keyed by ex_date; the index name is always an accepted alias.
        for alias in (index_name, *_TIMESTAMP_ALIASES):
            if alias in lowered:
                column = lowered[alias]
                out = frame.drop(columns=[column])
                out.index = pd.DatetimeIndex(pd.to_datetime(frame[column]), name=index_name)
                out.columns = [str(c).lower() for c in out.columns]
                return out
        if isinstance(frame.index, pd.DatetimeIndex):
            out = frame.copy()
            out.columns = [str(c).lower() for c in out.columns]
            out.index = out.index.rename(index_name)
            return out
        raise ProviderError(f"no timestamp column found; expected one of {_TIMESTAMP_ALIASES}")

    def _load_bars(self, symbol: str, frequency: str) -> pd.DataFrame:
        del frequency  # a file has the one frequency it was written at
        return self._index_by_time(self._read(self._path(symbol)), TIMESTAMP)

    def _load_actions(self, symbol: str) -> pd.DataFrame:
        path = self._path(symbol, ".actions")
        if not path.is_file():
            return actions_mod.empty_actions()
        return self._index_by_time(self._read(path), actions_mod.EX_DATE)
