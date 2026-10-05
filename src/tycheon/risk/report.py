"""The risk report: calibrated forecasts, VaR/ES, drawdown, volatility, stress, diagnostics.

``build_risk_report`` assembles everything for a portfolio into a :class:`RiskReport` that
renders to JSON (for machines) and to one self-contained HTML file (for people: inline SVG
plots, no scripts, no external resources, light and dark themes).

Every section carries what a reader needs to decide how far to trust it: the calibration
status of the forecast under it, the recent holdout coverage, the model mix, the ``as_of``,
the dependence assumption for portfolios, and the standing disclaimer. A plain-language
``warnings`` list says what is *not* trustworthy in this particular report, so the caveats are
not buried in tables.

For research and risk analytics. Not investment advice.
"""

from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

import tycheon
from tycheon.data.asof import as_utc
from tycheon.errors import OptionalDependencyError
from tycheon.models.base import DISCLAIMER
from tycheon.risk.measures import (
    DEFAULT_DRAWDOWNS,
    DEFAULT_LEVELS,
    MIN_TAIL_PATHS,
    RiskInput,
    drawdown_probabilities,
    loss_probabilities,
    value_at_risk_and_es,
    volatility_forecast,
)
from tycheon.risk.portfolio import aggregate_portfolio
from tycheon.risk.scenarios import run_stress_suite

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from tycheon.calibration.diagnostics import CalibrationReport
    from tycheon.models.base import ForecastDistribution
    from tycheon.risk.portfolio import Portfolio
    from tycheon.risk.scenarios import ScenarioResult

SCHEMA_VERSION = 1
_COVERAGES = (0.5, 0.8, 0.9)


def _clean(obj: Any) -> Any:
    """JSON-safe: NaN and infinity become ``null``; numpy and pandas scalars become Python."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | np.ndarray):
        return [_clean(v) for v in (obj.tolist() if isinstance(obj, np.ndarray) else obj)]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


@dataclass(frozen=True)
class RiskReport:
    """The assembled report: ``data`` is the JSON document, ``figures`` the inline SVG plots."""

    data: dict[str, Any]
    figures: dict[str, str] = field(default_factory=dict)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(_clean(self.data), indent=indent, allow_nan=False)

    def to_html(self) -> str:
        return _render_html(_clean(self.data), self.figures)


# ----------------------------------------------------------------------- building
def _forecast_section(dist: ForecastDistribution) -> dict[str, Any]:
    intervals = {}
    for c in _COVERAGES:
        try:
            lo, hi = dist.interval(c)
        except ValueError:
            continue
        intervals[f"{c:g}"] = {"low": lo, "high": hi}
    return {
        "index": [ts.isoformat() for ts in dist.index],
        "median": dist.quantile(0.5),
        "intervals": intervals,
        "n_sample_paths": 0 if dist.samples is None else int(dist.samples.shape[0]),
    }


def _risk_section(
    inp: RiskInput,
    levels: tuple[float, ...],
    drawdowns: tuple[float, ...],
    seed: int,
    model_sigma: Any = None,
) -> dict[str, Any]:
    return {
        "var_es": [m.to_dict() for m in value_at_risk_and_es(inp, levels, seed=seed)],
        "drawdown_probability": [e.to_dict() for e in drawdown_probabilities(inp, drawdowns)],
        "loss_probability": [e.to_dict() for e in loss_probabilities(inp, drawdowns)],
        "volatility": volatility_forecast(inp, model_sigma=model_sigma).to_dict(),
    }


def _calibration_warning(symbol: str, dist: ForecastDistribution) -> str | None:
    info = dist.calibration
    if dist.calibration_status == "calibrated":
        return None
    if dist.calibration_status == "stale" and info is not None and info.holdout_coverage:
        nominal = max(info.holdout_coverage, key=lambda c: abs(c - 0.9))
        return (
            f"{symbol}: calibration is STALE: recent holdout coverage was "
            f"{info.holdout_coverage[nominal]:.0%} at nominal {nominal:.0%}, outside the "
            f"{info.tolerance:.0%} tolerance. Treat its intervals with caution."
        )
    return f"{symbol}: the forecast is UNCALIBRATED: its intervals are the model's own opinion."


def _asset_section(
    symbol: str,
    dist: ForecastDistribution,
    levels: tuple[float, ...],
    drawdowns: tuple[float, ...],
    seed: int,
) -> dict[str, Any]:
    inp = RiskInput.from_forecast(dist, name=symbol)
    info = dist.calibration
    return {
        "last_close": dist.last_close,
        "model_id": dist.metadata.model_id,
        "model_mix": dist.model_mix,
        "model_card": dist.model_card,
        "calibration_status": dist.calibration_status,
        "calibration": None if info is None else info.to_dict(),
        "forecast": _forecast_section(dist),
        "risk": _risk_section(inp, levels, drawdowns, seed, dist.extras.get("sigma")),
        "notes": [] if info is None else list(info.notes),
    }


def _portfolio_warnings(
    pf_input: RiskInput, levels: tuple[float, ...], seed: int, dependence: str | None
) -> list[str]:
    out = []
    for m in value_at_risk_and_es(pf_input, levels, seed=seed):
        if not m.reliable:
            out.append(
                f"portfolio {m.kind} {m.level:.0%} rests on only {m.n_tail} tail paths "
                f"(fewer than {MIN_TAIL_PATHS}): not reliable. Use more sample paths."
            )
    if dependence is not None:
        out.append(
            "the portfolio is UNCALIBRATED as a whole: dependence between assets is assumed "
            f"({dependence}), not measured; the lower tail is likely understated."
        )
    return out


def build_risk_report(
    *,
    portfolio: Portfolio,
    forecasts: Mapping[str, ForecastDistribution],
    bars_by_symbol: Mapping[str, pd.DataFrame],
    as_of: datetime,
    calibration_reports: Mapping[str, CalibrationReport] | None = None,
    correlation: pd.DataFrame | None = None,
    levels: tuple[float, ...] = DEFAULT_LEVELS,
    drawdown_thresholds: tuple[float, ...] = DEFAULT_DRAWDOWNS,
    named_windows: Sequence[tuple[str, datetime, datetime]] = (),
    shocks: Mapping[str, Mapping[str, float]] | None = None,
    coupling: str = "terminal",
    seed: int = 0,
    with_plots: bool = True,
    title: str = "Tycheon risk report",
) -> RiskReport:
    """Assemble the full report for ``portfolio`` from per-asset forecasts.

    ``forecasts`` should already be calibrated (see :mod:`tycheon.calibration`); the report
    states whatever calibration status each carries rather than assuming one.
    """
    as_of_ts = as_utc(as_of)
    reports = dict(calibration_reports or {})
    multi = len(portfolio.symbols) > 1
    pf = aggregate_portfolio(
        portfolio, forecasts, correlation=correlation, seed=seed, coupling=coupling
    )
    single = None if multi else forecasts[portfolio.symbols[0]]
    pf_input = pf.to_risk_input(single_asset=single)

    assets = {
        s: _asset_section(s, forecasts[s], levels, drawdown_thresholds, seed)
        for s in portfolio.symbols
    }
    warnings = [w for s in portfolio.symbols if (w := _calibration_warning(s, forecasts[s]))]
    warnings += _portfolio_warnings(pf_input, levels, seed, pf.dependence if multi else None)

    stress = run_stress_suite(
        portfolio, bars_by_symbol, as_of=as_of_ts, horizon=len(pf.index),
        named_windows=named_windows, shocks=shocks, risk_input=pf_input,
    )  # fmt: skip
    if any(s.kind == "model-implied-tail" and not s.calibrated for s in stress):
        warnings.append(
            "the model-implied tail scenario is not calibrated; it shows what the model "
            "considers a bad outcome, not a measured probability."
        )

    total = portfolio.total_value
    corr = pf.correlation
    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "title": title,
        "disclaimer": DISCLAIMER,
        "as_of": as_of_ts.isoformat(),
        "generated_with": {"tycheon": tycheon.__version__},
        "horizon_bars": len(pf.index),
        "forecast_index": [ts.isoformat() for ts in pf.index],
        "portfolio": {
            "name": portfolio.name,
            "currency": portfolio.currency,
            "total_value": total,
            "positions": [
                {"symbol": p.symbol, "value": p.value, "weight": p.value / total}
                for p in portfolio.positions
            ],
        },
        "assets": assets,
        "portfolio_risk": {
            "calibration_status": pf_input.calibration_status,
            "dependence": pf.dependence,
            "coupling": coupling if multi else None,
            "model_mix": pf_input.model_mix,
            "asset_status": pf.asset_status,
            "notes": list(pf.notes),
            "correlation": None
            if corr is None
            else {"symbols": list(corr.columns), "matrix": corr.to_numpy().tolist()},
            "n_paths": pf_input.n_paths,
            **_risk_section(pf_input, levels, drawdown_thresholds, seed),
        },
        "stress": [s.to_dict() for s in stress],
        "diagnostics": {symbol: r.to_dict() for symbol, r in reports.items()},
        "warnings": warnings,
    }
    figures = {}
    if with_plots:
        figures = _make_figures(
            forecasts=forecasts,
            bars_by_symbol=bars_by_symbol,
            reports=reports,
            pf_input=pf_input,
            stress=stress,
            levels=levels,
            drawdowns=drawdown_thresholds,
        )
    return RiskReport(data=data, figures=figures)


def _make_figures(
    *,
    forecasts: Mapping[str, ForecastDistribution],
    bars_by_symbol: Mapping[str, pd.DataFrame],
    reports: Mapping[str, CalibrationReport],
    pf_input: RiskInput,
    stress: list[ScenarioResult],
    levels: tuple[float, ...],
    drawdowns: tuple[float, ...],
) -> dict[str, str]:
    from tycheon.calibration.plots import coverage_svg, pit_svg, reliability_svg
    from tycheon.risk.plots import drawdown_svg, fan_svg, pnl_svg, stress_svg

    figures: dict[str, str] = {}
    try:
        for symbol, dist in forecasts.items():
            close = bars_by_symbol[symbol]["close"].iloc[-60:]
            figures[f"fan:{symbol}"] = fan_svg(dist, close, f"{symbol}: {dist.metadata.model_id}")
            report = reports.get(symbol)
            if report is not None:
                figures[f"reliability:{symbol}"] = reliability_svg(report, f"{symbol}: reliability")
                figures[f"pit:{symbol}"] = pit_svg(report, f"{symbol}: PIT histogram")
                if report.coverages:
                    figures[f"coverage:{symbol}"] = coverage_svg(report, f"{symbol}: coverage")
        measures = value_at_risk_and_es(pf_input, levels)
        figures["pnl"] = pnl_svg(pf_input, measures, "Portfolio return over the horizon")
        figures["drawdown"] = drawdown_svg(drawdown_probabilities(pf_input, drawdowns))
        figures["stress"] = stress_svg(stress)
    except OptionalDependencyError:
        return {}
    return figures


# ------------------------------------------------------------------------ HTML
_CSS = """
:root{--bg:#fbfbfc;--fg:#1b1f24;--muted:#5b6470;--card:#fff;--line:#d9dde3;--ok:#1f7a55;
--warn:#a05a00;--bad:#b3261e;--accent:#2b6cb0}
@media (prefers-color-scheme:dark){:root{--bg:#0f1318;--fg:#e6e9ee;--muted:#9aa3af;
--card:#171c23;--line:#2b323c;--ok:#52c79a;--warn:#e0a24a;--bad:#ff7b72;--accent:#6aa9ec}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1040px;margin:0 auto;padding:24px 16px 56px}
h1{font-size:1.6rem;margin:.2em 0}
h2{font-size:1.2rem;margin:1.8em 0 .5em;border-bottom:1px solid var(--line);padding-bottom:.25em}
h3{font-size:1rem;margin:1.2em 0 .4em}.meta,.muted{color:var(--muted);font-size:.9rem}
.banner{border:1px solid var(--line);border-left:4px solid var(--accent);background:var(--card);
padding:10px 14px;border-radius:6px;margin:12px 0}.warn{border-left-color:var(--warn)}
table{border-collapse:collapse;width:100%;margin:.4em 0 1em;background:var(--card);font-size:.92rem}
th,td{border:1px solid var(--line);padding:6px 9px;text-align:right}
th{background:rgba(127,127,127,.08)}th:first-child,td:first-child{text-align:left}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;border:1px solid currentColor;
font-size:.8rem;font-weight:600}.calibrated{color:var(--ok)}.stale{color:var(--warn)}
.uncalibrated{color:var(--bad)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
figure{margin:0;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:8px}
figure svg{width:100%;height:auto}ul{padding-left:1.2em}
footer{margin-top:40px;border-top:1px solid var(--line);padding-top:12px}
@media (max-width:600px){main{padding:16px 12px 40px}table{font-size:.82rem}}
"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _pct(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None or not math.isfinite(x) else f"{100 * x:.{digits}f}%"


def _badge(status: str) -> str:
    return f'<span class="badge {_e(status)}">{_e(status)}</span>'


def _fig(figures: Mapping[str, str], key: str) -> str:
    svg = figures.get(key)
    return f"<figure>{svg}</figure>" if svg else ""


def _yes(flag: bool) -> str:
    return "yes" if flag else "NO"


def _var_table(rows: list[dict[str, Any]]) -> str:
    head = (
        "<table><tr><th>measure</th><th>level</th><th>loss (% of value)</th><th>std. error</th>"
        "<th>tail paths</th><th>reliable</th><th>calibrated</th></tr>"
    )
    body = "".join(
        f"<tr><td>{_e(r['kind'])}</td><td>{_pct(r['level'], 1)}</td>"
        f"<td>{_pct(r['loss_fraction'], 2)}</td><td>{_pct(r['standard_error'], 2)}</td>"
        f"<td>{int(r['n_tail_paths'])}</td><td>{_yes(r['reliable'])}</td>"
        f"<td>{_yes(r['calibrated'])}</td></tr>"
        for r in rows
    )
    return head + body + "</table>"


def _prob_table(rows: list[dict[str, Any]], label: str) -> str:
    head = f"<table><tr><th>{_e(label)}</th><th>probability</th><th>95% interval</th></tr>"
    body = "".join(
        f"<tr><td>{_pct(r['threshold'], 0)}</td><td>{_pct(r['probability'], 1)}</td>"
        f"<td>{_pct(r['ci_low'], 1)} to {_pct(r['ci_high'], 1)}</td></tr>"
        for r in rows
    )
    return head + body + "</table>"


def _vol_line(vol: dict[str, Any]) -> str:
    q = vol.get("path_realized_vol_quantiles") or {}
    spread = ""
    if q:
        spread = (
            f"; per-path realised volatility {_pct(q.get('0.05'), 2)} to "
            f"{_pct(q.get('0.95'), 2)} per bar"
        )
    model = vol.get("model_sigma_per_bar")
    extra = "" if model is None else f"; the model's own conditional sigma {_pct(model, 2)} per bar"
    return (
        f"<p class='muted'>Volatility: {_pct(vol['horizon_vol_per_bar'], 2)} per bar "
        f"({_pct(vol['annualized'], 1)} annualised){spread}{extra}.</p>"
    )


def _head(d: dict[str, Any]) -> str:
    p = d["portfolio"]
    warn = ""
    if d["warnings"]:
        items = "".join(f"<li>{_e(w)}</li>" for w in d["warnings"])
        warn = f"<div class='banner warn'><strong>Read these first</strong><ul>{items}</ul></div>"
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(d['title'])}</title><style>{_CSS}</style></head><body><main>"
        f"<h1>{_e(d['title'])}</h1>"
        f"<p class='meta'>As of {_e(d['as_of'])} &middot; horizon {d['horizon_bars']} bars "
        f"&middot; portfolio {_e(p['name'])} ({_e(p['currency'])} {p['total_value']:,.0f}) "
        f"&middot; tycheon {_e(d['generated_with']['tycheon'])}</p>"
        f"<div class='banner'><strong>{_e(d['disclaimer'])}</strong> Every number below is a "
        "model output with its uncertainty stated beside it; none of it is a recommendation.</div>"
        + warn
    )


def _trust_table(d: dict[str, Any]) -> str:
    rows = []
    for symbol, a in d["assets"].items():
        info = a["calibration"]
        if info and info["holdout_coverage"]:
            nominal = min(info["holdout_coverage"], key=lambda c: abs(float(c) - 0.9))
            cal = _pct(info["holdout_coverage"][nominal], 0)
            raw = _pct(info["raw_holdout_coverage"][nominal], 0)
            cov = f"{cal} / {raw} at nominal {_pct(float(nominal), 0)}"
            n = f"{info['n_scores']} (holdout {info['holdout_n']})"
        else:
            cov, n = "not measured", "n/a"
        mix = ", ".join(f"{_e(m)} {w:.0%}" for m, w in a["model_mix"].items())
        rows.append(
            f"<tr><td>{_e(symbol)}</td><td>{_badge(a['calibration_status'])}</td>"
            f"<td>{mix}</td><td>{cov}</td><td>{n}</td></tr>"
        )
    return (
        "<h2>How much to trust each forecast</h2><table><tr><th>asset</th><th>status</th>"
        "<th>model mix</th><th>holdout coverage (calibrated / raw)</th><th>scores</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def _correlation_table(pr: dict[str, Any]) -> str:
    corr = pr["correlation"]
    if not corr:
        return ""
    syms = corr["symbols"]
    head = "<h3>Correlation used to couple the assets</h3><table><tr><th></th>"
    head += "".join(f"<th>{_e(s)}</th>" for s in syms) + "</tr>"
    rows = "".join(
        f"<tr><td>{_e(s)}</td>" + "".join(f"<td>{v:.2f}</td>" for v in row) + "</tr>"
        for s, row in zip(syms, corr["matrix"], strict=True)
    )
    return head + rows + "</table>"


def _portfolio_section(d: dict[str, Any], figures: Mapping[str, str]) -> str:
    pr = d["portfolio_risk"]
    notes = "".join(f"<div class='banner warn'>{_e(n)}</div>" for n in pr["notes"])
    return (
        f"<h2>Portfolio risk {_badge(pr['calibration_status'])}</h2>"
        f"<p class='muted'>Dependence: {_e(pr['dependence'])}. "
        f"{pr['n_paths']} joint sample paths.</p>"
        + notes
        + "<h3>Value at Risk and Expected Shortfall</h3>"
        + _var_table(pr["var_es"])
        + _vol_line(pr["volatility"])
        + "<div class='grid'>"
        + _fig(figures, "pnl")
        + _fig(figures, "drawdown")
        + "</div><h3>Probability of a maximum drawdown beyond X</h3>"
        + _prob_table(pr["drawdown_probability"], "drawdown")
        + "<h3>Probability of ending the horizon down more than X</h3>"
        + _prob_table(pr["loss_probability"], "loss")
        + _correlation_table(pr)
    )


def _stress_section(d: dict[str, Any], figures: Mapping[str, str]) -> str:
    rows = []
    for s in d["stress"]:
        prob = "none" if s["probability"] is None else _pct(s["probability"], 1)
        rows.append(
            f"<tr><td>{_e(s['name'])}<br><span class='muted'>{_e(s['caveat'])}</span></td>"
            f"<td>{_e(s['kind'])}</td><td>{_pct(s['pnl_fraction'], 1)}</td>"
            f"<td>{_pct(s['max_drawdown'], 1)}</td><td>{prob}</td>"
            f"<td>{'yes' if s['calibrated'] else 'no'}</td></tr>"
        )
    return (
        "<h2>Stress scenarios</h2>"
        + _fig(figures, "stress")
        + "<table><tr><th>scenario</th><th>kind</th><th>P&amp;L</th><th>max drawdown</th>"
        "<th>probability</th><th>calibrated</th></tr>" + "".join(rows) + "</table>"
    )


def _diagnostics_table(diag: dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><td>{_pct(c['nominal'], 0)}</td><td>{_pct(c['raw_coverage'], 1)}</td>"
        f"<td>{_pct(c['calibrated_coverage'], 1)}</td><td>{c['mean_width_raw']:.4f}</td>"
        f"<td>{c['mean_width_calibrated']:.4f}</td></tr>"
        for c in diag["coverages"]
    )
    crps = ""
    if diag["crps_raw"] is not None and diag["crps_calibrated"] is not None:
        crps = (
            f" CRPS raw {diag['crps_raw']:.5f}, calibrated {diag['crps_calibrated']:.5f} "
            "(log-return units; lower is better)."
        )
    return (
        "<table><tr><th>nominal</th><th>raw coverage</th><th>calibrated coverage</th>"
        "<th>raw width</th><th>calibrated width</th></tr>"
        + rows
        + f"</table><p class='muted'>Measured on the most recent {diag['n_holdout']} origins "
        f"using only what was known at each; widths in log-return units.{crps}</p>"
    )


def _asset_html(
    symbol: str, a: dict[str, Any], diag: dict[str, Any] | None, figures: Mapping[str, str]
) -> str:
    notes = "".join(f"<div class='banner warn'>{_e(n)}</div>" for n in a["notes"])
    grid = "".join(_fig(figures, f"{k}:{symbol}") for k in ("reliability", "pit", "coverage"))
    return (
        f"<h2>{_e(symbol)} {_badge(a['calibration_status'])}</h2>"
        f"<p class='muted'>Model: {_e(a['model_id'])} &middot; card "
        f"<code>{_e(a['model_card'])}</code> &middot; last close {a['last_close']:.2f}</p>"
        + notes
        + _fig(figures, f"fan:{symbol}")
        + "<h3>Risk of this asset alone</h3>"
        + _var_table(a["risk"]["var_es"])
        + _vol_line(a["risk"]["volatility"])
        + f"<div class='grid'>{grid}</div>"
        + ("" if not diag else _diagnostics_table(diag))
    )


def _footer(d: dict[str, Any]) -> str:
    return (
        f"<footer><p><strong>{_e(d['disclaimer'])}</strong></p><p class='muted'>Intervals and "
        "risk measures describe a forecasting model's distribution. Calibration is checked on "
        "recent history and can fail when conditions change. Portfolio dependence is an "
        "assumption. Nothing here is a recommendation to buy or sell any security.</p></footer>"
        "</main></body></html>"
    )


def _render_html(d: dict[str, Any], figures: Mapping[str, str]) -> str:
    assets = "".join(
        _asset_html(symbol, a, d["diagnostics"].get(symbol), figures)
        for symbol, a in d["assets"].items()
    )
    return (
        _head(d)
        + _trust_table(d)
        + _portfolio_section(d, figures)
        + _stress_section(d, figures)
        + assets
        + _footer(d)
    )
