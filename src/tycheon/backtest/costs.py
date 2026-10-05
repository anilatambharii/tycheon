"""A simple cost and slippage model for strategy-style evaluation.

This exists to answer one question honestly: *would the forecast's edge survive frictions?*
It is a research diagnostic, not a trading system: nothing here sends an order, and Tycheon
has no live execution in v1.

The strategy is deliberately naive. At each origin it takes a position for one horizon:
long if the median forecast return exceeds ``min_edge``, short if it is below ``-min_edge``,
flat otherwise. Origins are ``horizon`` bars apart (no overlap). Each change of position pays
costs on the turnover. Costs are in basis points per unit of turnover, one way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from tycheon.calibration.scores import ScoreSet

Floats = NDArray[np.float64]

NOTE = "research diagnostic only: a naive one-horizon sign strategy, not a recommendation"


@dataclass(frozen=True)
class CostModel:
    """Frictions in basis points, charged per unit of turnover (one way).

    ``spread_bps`` is the full quoted spread (half is paid crossing it); ``slippage_bps`` is
    the extra adverse move per trade; ``fee_bps`` is any explicit commission.
    """

    spread_bps: float = 2.0
    slippage_bps: float = 1.0
    fee_bps: float = 0.0

    def __post_init__(self) -> None:
        if min(self.spread_bps, self.slippage_bps, self.fee_bps) < 0:
            raise ValueError("costs must not be negative")

    @property
    def one_way_bps(self) -> float:
        return self.spread_bps / 2.0 + self.slippage_bps + self.fee_bps

    @property
    def one_way(self) -> float:
        """One-way cost as a log-return fraction."""
        return self.one_way_bps / 1e4


@dataclass(frozen=True)
class StrategyResult:
    """Gross and net outcome of the naive strategy over a walk-forward run."""

    n_periods: int
    n_trades: int
    turnover: float
    gross_mean: float
    net_mean: float
    hit_rate: float
    net_sharpe_per_period: float
    cost_per_period: float

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "n_periods": self.n_periods,
            "n_trades": self.n_trades,
            "turnover": self.turnover,
            "gross_mean": self.gross_mean,
            "net_mean": self.net_mean,
            "hit_rate": self.hit_rate,
            "net_sharpe_per_period": self.net_sharpe_per_period,
            "cost_per_period": self.cost_per_period,
            "note": NOTE,
        }


def positions(median_return: Floats, min_edge: float = 0.0) -> Floats:
    """``+1`` long, ``-1`` short, ``0`` flat from the median forecast return."""
    return np.where(
        median_return > min_edge, 1.0, np.where(median_return < -min_edge, -1.0, 0.0)
    ).astype(np.float64)


def strategy_result(scores: ScoreSet, cost: CostModel, *, min_edge: float = 0.0) -> StrategyResult:
    """Run the naive sign strategy on a walk-forward :class:`ScoreSet`.

    Requires origins at least ``horizon`` bars apart (``stride >= horizon``): with overlap the
    positions would be held simultaneously and this accounting would be wrong.
    """
    if scores.stride < scores.horizon:
        raise ValueError(
            "the strategy accounting needs non-overlapping horizons (stride >= horizon)"
        )
    j = scores.levels.index(0.5)
    pos = positions(scores.base_logq[:, j, -1], min_edge)
    realized = scores.realized[:, -1]
    gross = pos * realized
    previous = np.concatenate([[0.0], pos[:-1]])
    turnover = np.abs(pos - previous)
    costs = turnover * cost.one_way
    # leaving a position at the end of each horizon is part of the same trade's round trip:
    # a position change is charged once, on the way in; closing the final position once.
    costs[-1] += abs(pos[-1]) * cost.one_way
    net = gross - costs
    traded = pos != 0
    sd = float(net.std(ddof=1)) if len(net) > 1 else 0.0
    return StrategyResult(
        n_periods=len(pos),
        n_trades=int((turnover > 0).sum()),
        turnover=float(turnover.sum()),
        gross_mean=float(gross.mean()),
        net_mean=float(net.mean()),
        hit_rate=float(np.mean(gross[traded] > 0)) if traded.any() else float("nan"),
        net_sharpe_per_period=float(net.mean() / sd) if sd > 0 else float("nan"),
        cost_per_period=float(costs.mean()),
    )
