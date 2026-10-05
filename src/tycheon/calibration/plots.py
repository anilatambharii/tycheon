"""Calibration diagnostic plots as inline SVG: reliability, PIT histogram, coverage.

Needs the ``report`` extra (matplotlib). Each function returns an SVG string.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from tycheon._svg import BLUE, GREY, ORANGE, figure_to_svg, new_figure

if TYPE_CHECKING:
    from tycheon.calibration.diagnostics import CalibrationReport


def reliability_svg(report: CalibrationReport, title: str = "Reliability") -> str:
    """Empirical frequency of ``outcome <= quantile`` against the quantile level.

    On the diagonal is calibrated; the raw curve shows how far the original forecast was off.
    """
    fig = new_figure(4.2, 4.0)
    ax = fig.subplots()
    levels = np.asarray(report.reliability_levels)
    ax.plot([0, 1], [0, 1], color=GREY, lw=1.0, ls="--", label="perfect")
    ax.plot(levels, report.reliability_raw, color=ORANGE, marker="o", ms=4, lw=1.5, label="raw")
    ax.plot(
        levels,
        report.reliability_calibrated,
        color=BLUE,
        marker="o",
        ms=4,
        lw=1.8,
        label="calibrated",
    )
    ax.set_xlabel("quantile level")
    ax.set_ylabel("observed frequency")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, frameon=False, loc="upper left")
    return figure_to_svg(fig)


def pit_svg(report: CalibrationReport, title: str = "PIT histogram") -> str:
    """Probability integral transform histogram. Flat is calibrated; a U is over-confident."""
    fig = new_figure(5.2, 3.4)
    ax = fig.subplots()
    bins = report.pit_bins
    edges = np.linspace(0, 1, bins + 1)
    centre = (edges[:-1] + edges[1:]) / 2
    width = 0.4 / bins
    raw = report.pit_raw / max(report.pit_raw.sum(), 1) * bins
    cal = report.pit_calibrated / max(report.pit_calibrated.sum(), 1) * bins
    ax.bar(centre - width / 1.6, raw, width=width * 1.5, color=ORANGE, label="raw")
    ax.bar(centre + width / 1.6, cal, width=width * 1.5, color=BLUE, label="calibrated")
    ax.axhline(1.0, color=GREY, lw=1.0, ls="--")
    ax.set_xlabel("PIT value")
    ax.set_ylabel("density (1 = uniform)")
    ax.set_xlim(0, 1)
    ax.set_title(title, fontsize=10)
    ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=0.25, axis="y")
    return figure_to_svg(fig)


def coverage_svg(report: CalibrationReport, title: str = "Interval coverage") -> str:
    """Achieved coverage of central intervals against their nominal level."""
    fig = new_figure(5.2, 3.4)
    ax = fig.subplots()
    nominal = np.array([c.nominal for c in report.coverages])
    raw = np.array([c.raw for c in report.coverages])
    cal = np.array([c.calibrated for c in report.coverages])
    x = np.arange(len(nominal))
    ax.bar(x - 0.2, raw, width=0.38, color=ORANGE, label="raw")
    ax.bar(x + 0.2, cal, width=0.38, color=BLUE, label="calibrated")
    ax.scatter(x, nominal, color="black", marker="_", s=900, lw=2, zorder=3, label="nominal")
    ax.set_xticks(x, [f"{n:.0%}" for n in nominal])
    ax.set_xlabel("nominal coverage")
    ax.set_ylabel("achieved coverage")
    ax.set_ylim(0, 1.05)
    ax.set_title(title, fontsize=10)
    ax.legend(fontsize=8, frameon=False, loc="lower right")
    ax.grid(alpha=0.25, axis="y")
    return figure_to_svg(fig)
