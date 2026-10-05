"""Shared helpers for the diagnostic plots: matplotlib is optional, output is inline SVG.

Plots are returned as SVG *strings* so an HTML report can embed them with no external files,
no scripts and no network access. Text and lines use ``currentColor`` so a figure follows the
page's light or dark theme.
"""

from __future__ import annotations

import importlib
import io
import re
from typing import TYPE_CHECKING, Any

from tycheon.errors import OptionalDependencyError

if TYPE_CHECKING:
    from matplotlib.figure import Figure

#: Colours chosen to read on both light and dark backgrounds.
BLUE = "#3b82c4"
ORANGE = "#e08a2c"
RED = "#d1495b"
GREEN = "#3aa17e"
GREY = "#8a8f98"


def require_matplotlib() -> Any:
    """Import ``matplotlib`` or explain which extra installs it."""
    try:
        return importlib.import_module("matplotlib")
    except ImportError as exc:
        raise OptionalDependencyError(
            "plots need matplotlib. Install the extra: pip install 'tycheon[report]'"
        ) from exc


def new_figure(width: float = 6.0, height: float = 3.6) -> Figure:
    """A figure not attached to pyplot (no global state, safe in servers and tests)."""
    require_matplotlib()
    from matplotlib.figure import Figure

    return Figure(figsize=(width, height), layout="constrained")


def figure_to_svg(fig: Figure) -> str:
    """Serialise ``fig`` to an inline-able SVG string that follows the page colour scheme."""
    buffer = io.StringIO()
    fig.savefig(buffer, format="svg", transparent=True)
    svg = buffer.getvalue()
    start = svg.find("<svg")
    body = svg[start:] if start >= 0 else svg
    body = re.sub(r"<metadata>.*?</metadata>\s*", "", body, flags=re.DOTALL)
    # black text and axes become the surrounding text colour (light and dark themes)
    return body.replace("#000000", "currentColor").replace("rgb(0%, 0%, 0%)", "currentColor")
