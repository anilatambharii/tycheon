"""Corporate actions, and the one adjustment that is safe to do point-in-time.

Split- and dividend-adjusted price history is a classic source of leakage: the
adjusted series a vendor shows *today* embeds every action announced up to today,
so a "historical" forecast quietly knows about splits that had not happened yet.
:func:`adjust_bars` therefore takes an ``as_of`` and applies only the actions that
were both announced (``available_at``) and effective (``ex_date``) by then.

An *actions frame* is indexed by ``ex_date`` (timezone-aware) with columns:

* ``kind``: ``"split"`` or ``"dividend"``;
* ``value``: for a split the share ratio (``2.0`` is a 2-for-1), for a dividend the
  cash amount per share;
* ``available_at``: when the action was announced.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tycheon.data.asof import AVAILABLE_AT
from tycheon.data.schema import PRICE_COLUMNS
from tycheon.errors import DataValidationError

EX_DATE = "ex_date"
ACTION_KINDS: tuple[str, ...] = ("split", "dividend")
ACTION_COLUMNS: tuple[str, ...] = ("kind", "value", AVAILABLE_AT)


def empty_actions() -> pd.DataFrame:
    """An actions frame with no rows, correctly typed."""
    index = pd.DatetimeIndex([], tz="UTC", name=EX_DATE)
    return pd.DataFrame(
        {
            "kind": pd.Series([], dtype="object", index=index),
            "value": pd.Series([], dtype="float64", index=index),
            AVAILABLE_AT: pd.Series([], dtype="datetime64[ns, UTC]", index=index),
        }
    )


def normalize_actions(frame: pd.DataFrame, *, tz: str = "UTC") -> pd.DataFrame:
    """Canonicalise an actions frame: UTC ``ex_date`` index, typed columns, validated."""
    required = [c for c in ("kind", "value") if c not in frame.columns]
    if required:
        raise DataValidationError(f"actions are missing columns {required}")
    out = frame.copy()
    index = pd.DatetimeIndex(out.index)
    if index.tz is None:
        index = index.tz_localize(tz)
    out.index = index.tz_convert("UTC").rename(EX_DATE)
    out["value"] = pd.to_numeric(out["value"], errors="raise").astype("float64")
    if AVAILABLE_AT not in out.columns:
        # Without an announcement time the ex-date is the latest it could have been
        # known, which is the conservative choice.
        out[AVAILABLE_AT] = out.index
    out[AVAILABLE_AT] = pd.to_datetime(out[AVAILABLE_AT], utc=True)
    out = out[list(ACTION_COLUMNS)].sort_index(kind="stable")
    validate_actions(out)
    return out


def validate_actions(frame: pd.DataFrame) -> None:
    """Raise :class:`DataValidationError` unless ``frame`` is a well-formed actions frame."""
    missing = [c for c in ACTION_COLUMNS if c not in frame.columns]
    if missing:
        raise DataValidationError(f"actions are missing columns {missing}")
    unknown = sorted(set(frame["kind"]) - set(ACTION_KINDS))
    if unknown:
        raise DataValidationError(f"unknown action kinds {unknown}; expected {ACTION_KINDS}")
    if (frame["value"] <= 0).any():
        raise DataValidationError("action values must be positive")
    if frame["value"].isna().any():
        raise DataValidationError("NaN in action values")


def adjust_bars(
    bars: pd.DataFrame,
    actions: pd.DataFrame,
    as_of: pd.Timestamp,
    *,
    adjust_dividends: bool = False,
) -> pd.DataFrame:
    """Return ``bars`` adjusted for the actions known and effective at ``as_of``.

    Splits divide earlier prices and multiply earlier volume by the ratio. Cash
    dividends, when requested, scale earlier prices by ``1 - dividend / prior
    close``. Bars on or after an action's ex-date are untouched.
    """
    known = actions.loc[
        (pd.to_datetime(actions[AVAILABLE_AT], utc=True) <= as_of) & (actions.index <= as_of)
    ]
    if not adjust_dividends:
        known = known.loc[known["kind"] == "split"]

    out = bars.copy()
    price_factor = pd.Series(1.0, index=out.index)
    volume_factor = pd.Series(1.0, index=out.index)

    ordered = known.sort_index()
    ex_dates = pd.DatetimeIndex(ordered.index)
    for position in range(len(ordered)):
        ex_date, row = ex_dates[position], ordered.iloc[position]
        before = out.index < ex_date
        if not before.any():
            continue
        ratio = float(row["value"])
        if row["kind"] == "split":
            price_factor[before] /= ratio
            volume_factor[before] *= ratio
        else:
            prior_close = float(out.loc[before, "close"].iloc[-1])
            if ratio >= prior_close:
                raise DataValidationError(
                    f"dividend {ratio} on {ex_date.date()} is not below "
                    f"the prior close {prior_close}"
                )
            price_factor[before] *= 1.0 - ratio / prior_close

    for col in PRICE_COLUMNS:
        out[col] = out[col].to_numpy(dtype="float64") * price_factor.to_numpy()
    for col in ("volume",):
        if col in out.columns:
            out[col] = out[col].to_numpy(dtype="float64") * volume_factor.to_numpy()
    if "amount" in out.columns:
        # Traded value is a currency amount, invariant under a split.
        out["amount"] = out["amount"].to_numpy(dtype="float64")
    out.attrs["adjusted_for"] = [ts.isoformat() for ts in known.index]
    return out


def cumulative_split_factor(actions: pd.DataFrame, as_of: pd.Timestamp) -> float:
    """Product of the split ratios known and effective at ``as_of`` (for diagnostics)."""
    known = actions.loc[
        (actions["kind"] == "split")
        & (pd.to_datetime(actions[AVAILABLE_AT], utc=True) <= as_of)
        & (actions.index <= as_of)
    ]
    return float(np.prod(known["value"].to_numpy(dtype="float64"))) if len(known) else 1.0
