"""Stress scenarios: historical replay, hypothetical shocks, and model-implied tails.

Three families, deliberately kept apart because they answer different questions and carry
different caveats:

* **Historical replay.** What would today's portfolio have lost over a window that actually
  happened (a named crisis, or the worst windows found automatically)? It needs no model, but
  it only knows what *has* happened, and every bar used must have been published by ``as_of``.
* **Hypothetical shocks.** Instantaneous moves you choose ("equities -20%"). No probability.
* **Model-implied tails.** The worst fraction of a forecaster's sample paths: what the
  forecast model itself considers a bad outcome, with the probability it assigns. This is the
  scenario generator for Kronos or any path-capable forecaster. It inherits the forecast's
  calibration status, so an uncalibrated model's "tail" is labelled as such.

For research and risk analytics. Not investment advice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from tycheon.data.asof import as_utc, assert_available
from tycheon.errors import DataValidationError, LookaheadError
from tycheon.risk.measures import RiskInput, max_drawdown

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from tycheon.risk.portfolio import Portfolio

Floats = NDArray[np.float64]


@dataclass(frozen=True)
class ScenarioResult:
    """The portfolio outcome under one scenario, as fractions of its starting value."""

    name: str
    kind: str
    pnl_fraction: float
    max_drawdown: float
    path: tuple[float, ...] = field(default_factory=tuple)
    window: tuple[str, str] | None = None
    probability: float | None = None
    calibrated: bool = False
    caveat: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "pnl_fraction": self.pnl_fraction,
            "max_drawdown": self.max_drawdown,
            "path": list(self.path),
            "window": None if self.window is None else list(self.window),
            "probability": self.probability,
            "calibrated": self.calibrated,
            "caveat": self.caveat,
        }


def _drawdown(values: Floats) -> float:
    peak = np.maximum.accumulate(values)
    return float((1.0 - values / peak).max())


def _portfolio_path(
    portfolio: Portfolio, closes: pd.DataFrame, base_row: int, rows: slice
) -> Floats:
    """Value of a buy-and-hold portfolio along ``closes[rows]``, relative to row ``base_row``."""
    base = closes.iloc[base_row]
    path = np.zeros(len(closes.iloc[rows]))
    for p in portfolio.positions:
        path += p.value * closes[p.symbol].iloc[rows].to_numpy() / base[p.symbol]
    return path / portfolio.total_value


def _closes(
    bars_by_symbol: Mapping[str, pd.DataFrame], portfolio: Portfolio, as_of: pd.Timestamp
) -> pd.DataFrame:
    missing = [s for s in portfolio.symbols if s not in bars_by_symbol]
    if missing:
        raise DataValidationError(f"no history for {missing}")
    for s in portfolio.symbols:
        assert_available(bars_by_symbol[s], as_of)
    return pd.DataFrame({s: bars_by_symbol[s]["close"] for s in portfolio.symbols}).dropna()


def historical_replay(
    portfolio: Portfolio,
    bars_by_symbol: Mapping[str, pd.DataFrame],
    *,
    start: datetime,
    end: datetime,
    as_of: datetime,
    name: str | None = None,
) -> ScenarioResult:
    """Replay the returns of ``[start, end]`` on today's positions.

    The window must lie entirely at or before ``as_of`` (``LookaheadError`` otherwise) and
    inside the history, with a bar before ``start`` to measure the first move from.
    """
    as_of_ts, start_ts, end_ts = as_utc(as_of), as_utc(start, "start"), as_utc(end, "end")
    if end_ts > as_of_ts:
        raise LookaheadError(
            f"scenario window ends {end_ts.isoformat()}, after as_of {as_of_ts.isoformat()}"
        )
    closes = _closes(bars_by_symbol, portfolio, as_of_ts)
    first = int(closes.index.searchsorted(start_ts, side="left"))
    last = int(closes.index.searchsorted(end_ts, side="right")) - 1
    if first < 1 or last < first:
        raise DataValidationError(
            "the window needs at least one bar, and one bar before it, in the history"
        )
    rel = _portfolio_path(portfolio, closes, first - 1, slice(first, last + 1))
    return ScenarioResult(
        name=name or f"replay {start_ts:%Y-%m-%d} to {end_ts:%Y-%m-%d}",
        kind="historical-replay",
        pnl_fraction=float(rel[-1] - 1.0),
        max_drawdown=_drawdown(np.concatenate([[1.0], rel])),
        path=tuple(float(x - 1.0) for x in rel),
        window=(closes.index[first].isoformat(), closes.index[last].isoformat()),
        caveat="what happened in that window, applied to today's positions; not a forecast",
    )


def worst_windows(
    portfolio: Portfolio,
    bars_by_symbol: Mapping[str, pd.DataFrame],
    *,
    horizon: int,
    as_of: datetime,
    k: int = 3,
) -> list[ScenarioResult]:
    """The ``k`` worst non-overlapping ``horizon``-bar windows in the history, replayed."""
    as_of_ts = as_utc(as_of)
    closes = _closes(bars_by_symbol, portfolio, as_of_ts)
    n = len(closes)
    if n <= horizon + 1:
        raise DataValidationError("history is too short for this horizon")
    weights = portfolio.weights
    window_return = np.zeros(n - horizon)
    for s in portfolio.symbols:
        c = closes[s].to_numpy()
        window_return += weights[s] * (c[horizon:] / c[:-horizon] - 1.0)
    chosen: list[int] = []
    for start in np.argsort(window_return):
        if all(abs(int(start) - c) >= horizon for c in chosen):
            chosen.append(int(start))
        if len(chosen) == k:
            break
    out = []
    for rank, start in enumerate(chosen, 1):
        rel = _portfolio_path(portfolio, closes, start, slice(start + 1, start + horizon + 1))
        out.append(
            ScenarioResult(
                name=f"worst {horizon}-bar window #{rank}",
                kind="worst-window",
                pnl_fraction=float(rel[-1] - 1.0),
                max_drawdown=_drawdown(np.concatenate([[1.0], rel])),
                path=tuple(float(x - 1.0) for x in rel),
                window=(
                    closes.index[start + 1].isoformat(),
                    closes.index[start + horizon].isoformat(),
                ),
                caveat="found automatically in the history known at as_of; the worst the past "
                "offers, not the worst possible",
            )
        )
    return out


def shock(portfolio: Portfolio, shocks: Mapping[str, float], *, name: str) -> ScenarioResult:
    """An instantaneous move: each symbol's simple return (``-0.2`` is a 20% fall)."""
    unknown = [s for s in shocks if s not in portfolio.symbols]
    if unknown:
        raise DataValidationError(f"shock names symbols not in the portfolio: {unknown}")
    weights = portfolio.weights
    pnl = float(sum(weights[s] * r for s, r in shocks.items()))
    return ScenarioResult(
        name=name,
        kind="shock",
        pnl_fraction=pnl,
        max_drawdown=max(0.0, -pnl),
        path=(pnl,),
        caveat="a hypothetical instantaneous move; carries no probability",
    )


def model_implied_tail(
    inp: RiskInput,
    *,
    tail_fraction: float = 0.05,
    metric: str = "terminal",
    name: str | None = None,
) -> ScenarioResult:
    """The worst ``tail_fraction`` of the forecaster's own paths, summarised as a scenario.

    ``metric`` ranks paths by ``"terminal"`` return or by ``"drawdown"``. The scenario path is
    the average of the selected paths. Its ``probability`` is what the forecast assigns to this
    tail, and it is labelled calibrated only if the forecast was calibrated at that tail.
    """
    if not 0.0 < tail_fraction < 0.5:
        raise ValueError("tail_fraction must be in (0, 0.5)")
    k = max(1, int(np.ceil(tail_fraction * inp.n_paths)))
    if metric == "terminal":
        chosen = np.argsort(inp.returns())[:k]
    elif metric == "drawdown":
        chosen = np.argsort(-max_drawdown(inp))[:k]
    else:
        raise ValueError("metric must be 'terminal' or 'drawdown'")
    rel = inp.paths[chosen].mean(axis=0) / inp.start_value
    calibrated = inp.tail_is_calibrated(tail_fraction)
    return ScenarioResult(
        name=name or f"model-implied worst {tail_fraction:.0%} ({metric})",
        kind="model-implied-tail",
        pnl_fraction=float(rel[-1] - 1.0),
        max_drawdown=_drawdown(np.concatenate([[1.0], rel])),
        path=tuple(float(x - 1.0) for x in rel),
        probability=k / inp.n_paths,
        calibrated=calibrated,
        caveat=(
            f"the worst {k} of {inp.n_paths} paths from {inp.name} ({inp.calibration_status}); "
            + ("tail within the calibrated range" if calibrated else "this tail is NOT calibrated")
        ),
    )


def run_stress_suite(
    portfolio: Portfolio,
    bars_by_symbol: Mapping[str, pd.DataFrame],
    *,
    as_of: datetime,
    horizon: int,
    named_windows: Sequence[tuple[str, datetime, datetime]] = (),
    shocks: Mapping[str, Mapping[str, float]] | None = None,
    risk_input: RiskInput | None = None,
    worst_k: int = 3,
) -> list[ScenarioResult]:
    """All three families in one call. Skips a family if its inputs are not supplied."""
    results: list[ScenarioResult] = []
    for label, start, end in named_windows:
        results.append(
            historical_replay(
                portfolio, bars_by_symbol, start=start, end=end, as_of=as_of, name=label
            )
        )
    results.extend(
        worst_windows(portfolio, bars_by_symbol, horizon=horizon, as_of=as_of, k=worst_k)
    )
    for label, moves in (shocks or {}).items():
        results.append(shock(portfolio, moves, name=label))
    if risk_input is not None:
        results.append(model_implied_tail(risk_input, tail_fraction=0.05))
    return results
