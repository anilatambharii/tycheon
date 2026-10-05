"""Risk plots as inline SVG: fan charts, P&L distribution, drawdown and stress bars.

Needs the ``report`` extra (matplotlib). Each function returns an SVG string.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from tycheon._svg import BLUE, GREEN, GREY, ORANGE, RED, figure_to_svg, new_figure

if TYPE_CHECKING:
    import pandas as pd

    from tycheon.models.base import ForecastDistribution
    from tycheon.risk.measures import ProbabilityEstimate, RiskInput, RiskMeasure
    from tycheon.risk.scenarios import ScenarioResult


def fan_svg(dist: ForecastDistribution, recent: pd.Series | None = None, title: str = "") -> str:
    """Median and central intervals of a forecast, with recent history for context."""
    fig = new_figure(6.2, 3.6)
    ax = fig.subplots()
    if recent is not None and len(recent):
        ax.plot(recent.index, recent.to_numpy(), color="black", lw=1.2, label="history")
    widest = dist.max_coverage
    lo, hi = dist.interval(widest)
    ax.fill_between(dist.index, lo, hi, color=BLUE, alpha=0.18, label=f"{widest:.0%} interval")
    try:
        lo50, hi50 = dist.interval(0.5)
        ax.fill_between(dist.index, lo50, hi50, color=BLUE, alpha=0.32, label="50% interval")
    except ValueError:
        pass
    ax.plot(dist.index, dist.quantile(0.5), color=BLUE, lw=2.0, label="median")
    as_of: Any = dist.as_of  # matplotlib accepts datetimes on a date axis; its stubs say float
    ax.axvline(as_of, color=GREY, lw=0.8, ls=":")
    ax.set_title(title or f"{dist.metadata.model_id} [{dist.calibration_status}]", fontsize=10)
    ax.tick_params(axis="x", rotation=30, labelsize=8)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, frameon=False, loc="best")
    return figure_to_svg(fig)


def pnl_svg(inp: RiskInput, measures: list[RiskMeasure], title: str = "Return distribution") -> str:
    """Histogram of horizon returns with the VaR and ES lines of the chosen levels."""
    fig = new_figure(6.2, 3.6)
    ax = fig.subplots()
    returns = inp.returns() * 100.0
    ax.hist(returns, bins=60, color=BLUE, alpha=0.55, density=True)
    shown = {0.95, 0.99}
    for m in measures:
        if m.level in shown:
            colour = ORANGE if m.kind == "VaR" else RED
            style = "-" if m.kind == "VaR" else "--"
            ax.axvline(
                -m.value * 100.0, color=colour, lw=1.6, ls=style, label=f"{m.kind} {m.level:.0%}"
            )
    ax.axvline(0.0, color=GREY, lw=0.8)
    ax.set_xlabel("return over the horizon (%)")
    ax.set_ylabel("density")
    ax.set_title(f"{title} [{inp.calibration_status}]", fontsize=10)
    ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=0.25)
    return figure_to_svg(fig)


def drawdown_svg(
    probs: list[ProbabilityEstimate], title: str = "Probability of drawdown beyond X"
) -> str:
    """Probability bars with their Wilson intervals."""
    fig = new_figure(5.2, 3.2)
    ax = fig.subplots()
    x = np.arange(len(probs))
    p = np.array([e.probability for e in probs])
    err = np.array(
        [[e.probability - e.low for e in probs], [e.high - e.probability for e in probs]]
    )
    ax.bar(x, p, color=ORANGE, alpha=0.8, yerr=err, capsize=4)
    ax.set_xticks(x, [f"{e.threshold:.0%}" for e in probs])
    ax.set_xlabel("drawdown threshold")
    ax.set_ylabel("probability")
    ax.set_ylim(0, max(0.05, float(np.max([e.high for e in probs])) * 1.15))
    ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.25, axis="y")
    return figure_to_svg(fig)


def stress_svg(results: list[ScenarioResult], title: str = "Stress scenarios") -> str:
    """Portfolio P&L under each scenario, coloured by how it was produced."""
    colours = {
        "historical-replay": BLUE,
        "worst-window": ORANGE,
        "shock": RED,
        "model-implied-tail": GREEN,
    }
    fig = new_figure(6.4, max(2.4, 0.42 * len(results) + 1.2))
    ax = fig.subplots()
    y = np.arange(len(results))[::-1]
    ax.barh(
        y,
        [r.pnl_fraction * 100.0 for r in results],
        color=[colours.get(r.kind, GREY) for r in results],
        alpha=0.85,
    )
    ax.set_yticks(y, [r.name for r in results], fontsize=8)
    ax.axvline(0.0, color=GREY, lw=0.8)
    ax.set_xlabel("portfolio P&L (%)")
    ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.25, axis="x")
    return figure_to_svg(fig)
