"""Customer data sources: validated CSV uploads, stored per tenant, read point-in-time.

Customers bring their own data under their own licence; Tycheon Cloud stores it for that customer
only and never serves it to anyone else. Files are validated with the OSS ``FileProvider`` itself,
so what is accepted is exactly what the forecaster will later read.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from tycheon.data.providers.file import FileProvider
from tycheon.errors import TycheonError
from tycheon_cp.blob import org_key

if TYPE_CHECKING:
    from tycheon_cp.blob import BlobStore

MIN_ROWS = 60
MAX_ROWS = 2_000_000
_FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)
#: validation reads the upload under a fixed name: the customer's symbol never touches a path here
_UPLOAD_NAME = "UPLOAD"


class DataSourceError(ValueError):
    """An upload is unusable. The message is safe to return to the customer."""


@dataclass(frozen=True)
class CsvInfo:
    n_rows: int
    first_ts: datetime
    last_ts: datetime
    frequency: str
    sha256: str


def infer_frequency(index: pd.DatetimeIndex) -> str:
    """The bar spacing of a file: ``1D``, ``1H`` or ``1min`` (the median gap decides)."""
    gaps = index.to_series().diff().dropna()
    if gaps.empty:
        raise DataSourceError("the file has no bars to infer a frequency from")
    median = gaps.median()
    if median >= pd.Timedelta(hours=20):
        return "1D"
    if median >= pd.Timedelta(minutes=50):
        return "1H"
    return "1min"


def validate_csv(raw: bytes, *, max_bytes: int) -> CsvInfo:
    """Parse ``raw`` as the OSS file provider would, and check it is fit to forecast from."""
    if not raw:
        raise DataSourceError("the file is empty")
    if len(raw) > max_bytes:
        raise DataSourceError(f"the file is larger than {max_bytes // 1_000_000} MB")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / f"{_UPLOAD_NAME}.csv").write_bytes(raw)
        try:
            bars = FileProvider(tmp).fetch_bars(
                _UPLOAD_NAME, start=None, end=None, as_of=_FAR_FUTURE, frequency="1D"
            )
        except (TycheonError, ValueError, KeyError, pd.errors.ParserError) as exc:
            raise DataSourceError(f"the file is not a valid bars file: {exc}") from exc
    if len(bars) < MIN_ROWS:
        raise DataSourceError(f"at least {MIN_ROWS} bars are needed; the file has {len(bars)}")
    if len(bars) > MAX_ROWS:
        raise DataSourceError(f"at most {MAX_ROWS} bars are accepted")
    if not bars.index.is_monotonic_increasing:
        raise DataSourceError("timestamps must be in increasing order")
    if bars.index.has_duplicates:
        raise DataSourceError("timestamps must be unique")
    return CsvInfo(
        n_rows=len(bars),
        first_ts=bars.index[0].to_pydatetime(),
        last_ts=bars.index[-1].to_pydatetime(),
        frequency=infer_frequency(pd.DatetimeIndex(bars.index)),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def source_directory(store: BlobStore, org_id: str, data_source_id: str) -> Path:
    """The tenant's own directory of ``<SYMBOL>.csv`` files for one data source."""
    return store.directory(org_key(org_id, "ds", data_source_id))
