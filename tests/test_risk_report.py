"""The risk report: JSON schema, honest warnings, a self-contained and safe HTML page."""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd
import pytest

from tycheon.calibration import ConformalCalibrator, SplitConformal, collect_scores
from tycheon.models.base import DISCLAIMER
from tycheon.risk import Portfolio, build_risk_report, estimate_correlation

H = 5


@pytest.fixture(scope="module")
def world(bars_from_returns, gaussian_cls):
    rng = np.random.default_rng(3)
    z = rng.multivariate_normal([0, 0], [[1, 0.6], [0.6, 1]], size=1500)
    bars = {"AAA": bars_from_returns(0.01 * z[:, 0]), "BBB": bars_from_returns(0.015 * z[:, 1])}
    as_of = bars["AAA"]["available_at"].iloc[-1]
    forecasts, reports = {}, {}
    for symbol, sigma in (("AAA", 0.01), ("BBB", 0.015)):
        model = gaussian_cls(f"gauss-{symbol}", sigma)
        scores = collect_scores(
            model, bars[symbol], as_of=as_of, horizon=H, n_origins=150, max_history=40,
            n_samples=60,
        )  # fmt: skip
        cal = ConformalCalibrator(SplitConformal()).fit(scores)
        raw = model.predict(bars[symbol], H, 600, as_of, seed=1)
        forecasts[symbol] = cal.calibrate(raw)
        reports[symbol] = cal.report
    return bars, forecasts, reports, as_of


def _report(world, symbols=("AAA", "BBB"), **kwargs):
    bars, forecasts, reports, as_of = world
    portfolio = Portfolio.from_values(dict.fromkeys(symbols, 1000.0))
    return build_risk_report(
        portfolio=portfolio,
        forecasts={s: forecasts[s] for s in symbols},
        bars_by_symbol={s: bars[s] for s in symbols},
        as_of=as_of,
        calibration_reports={s: reports[s] for s in symbols},
        correlation=estimate_correlation(bars, as_of=as_of) if len(symbols) > 1 else None,
        **kwargs,
    )


def test_the_json_has_the_documented_shape(world) -> None:
    data = json.loads(_report(world, with_plots=False).to_json())
    assert data["schema_version"] == 1
    assert data["disclaimer"] == DISCLAIMER
    assert set(data["assets"]) == {"AAA", "BBB"}
    a = data["assets"]["AAA"]
    for key in ("calibration_status", "calibration", "model_mix", "model_card", "forecast", "risk"):
        assert key in a
    assert {"var_es", "drawdown_probability", "loss_probability", "volatility"} <= set(a["risk"])
    assert a["calibration"]["holdout_coverage"]
    assert data["portfolio_risk"]["n_paths"] == 600
    assert data["stress"] and data["diagnostics"]["AAA"]["coverages"]


def test_every_number_in_the_json_is_finite_or_null(world) -> None:
    text = _report(world, with_plots=False).to_json()
    assert "NaN" not in text and "Infinity" not in text


def test_a_multi_asset_portfolio_is_reported_uncalibrated_with_a_warning(world) -> None:
    data = _report(world, with_plots=False).data
    assert data["portfolio_risk"]["calibration_status"] == "uncalibrated"
    assert any("UNCALIBRATED as a whole" in w for w in data["warnings"])


def test_a_single_asset_portfolio_keeps_its_calibration(world) -> None:
    data = _report(world, symbols=("AAA",), with_plots=False).data
    assert (
        data["portfolio_risk"]["calibration_status"] == data["assets"]["AAA"]["calibration_status"]
    )
    assert not any("UNCALIBRATED as a whole" in w for w in data["warnings"])


def test_an_uncalibrated_forecast_is_called_out(world, gaussian_cls) -> None:
    bars, _, _, as_of = world
    raw = gaussian_cls("raw", 0.01).predict(bars["AAA"], H, 300, as_of, seed=2)
    portfolio = Portfolio.from_values({"AAA": 1000.0})
    data = build_risk_report(
        portfolio=portfolio, forecasts={"AAA": raw}, bars_by_symbol={"AAA": bars["AAA"]},
        as_of=as_of, with_plots=False,
    ).data  # fmt: skip
    assert data["assets"]["AAA"]["calibration_status"] == "uncalibrated"
    assert any("AAA: the forecast is UNCALIBRATED" in w for w in data["warnings"])


def test_the_html_is_self_contained_and_carries_the_disclaimer(world) -> None:
    pytest.importorskip("matplotlib")
    html = _report(world).to_html()
    assert html.count(DISCLAIMER) >= 2
    assert "<script" not in html.lower()
    assert "<svg" in html
    # nothing is fetched: no external references of any kind
    assert not re.findall(r"""(?:src|href)\s*=\s*["']https?://""", html)
    assert "@import" not in html and "url(http" not in html
    assert "prefers-color-scheme:dark" in html
    for needle in ("Value at Risk", "Stress scenarios", "AAA", "BBB", "holdout coverage"):
        assert needle in html


def test_the_html_renders_without_plots_when_matplotlib_is_absent(world, monkeypatch) -> None:
    from tycheon.errors import OptionalDependencyError

    def boom(*args, **kwargs):
        raise OptionalDependencyError("no matplotlib")

    monkeypatch.setattr("tycheon.risk.plots.fan_svg", boom)
    pytest.importorskip("matplotlib")
    out = _report(world)
    assert out.figures == {}
    assert "Value at Risk" in out.to_html()


def test_dynamic_text_is_html_escaped(world) -> None:
    out = _report(world, with_plots=False, title="<img src=x onerror=alert(1)>")
    html = out.to_html()
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


def test_a_stale_forecast_produces_a_stale_warning(world) -> None:
    from dataclasses import replace

    bars, forecasts, _, as_of = world
    dist = forecasts["AAA"]
    info = replace(dist.calibration, holdout_coverage={0.9: 0.55}, tolerance=0.05)
    stale = replace(dist, calibration_status="stale", calibration=info)
    portfolio = Portfolio.from_values({"AAA": 1000.0})
    data = build_risk_report(
        portfolio=portfolio, forecasts={"AAA": stale}, bars_by_symbol={"AAA": bars["AAA"]},
        as_of=as_of, with_plots=False,
    ).data  # fmt: skip
    assert any("STALE" in w and "55%" in w for w in data["warnings"])


def test_few_paths_flag_unreliable_tails(world, gaussian_cls) -> None:
    bars, _, _, as_of = world
    few = gaussian_cls("few", 0.01).predict(bars["AAA"], H, 60, as_of, seed=2)
    data = build_risk_report(
        portfolio=Portfolio.from_values({"AAA": 1000.0}), forecasts={"AAA": few},
        bars_by_symbol={"AAA": bars["AAA"]}, as_of=as_of, with_plots=False,
        levels=(0.99,),
    ).data  # fmt: skip
    assert any("not reliable" in w for w in data["warnings"])


def test_the_report_is_deterministic_for_a_seed(world) -> None:
    a = _report(world, with_plots=False, seed=4).to_json()
    b = _report(world, with_plots=False, seed=4).to_json()
    assert a == b


def test_the_index_is_iso_timestamps(world) -> None:
    data = _report(world, with_plots=False).data
    assert len(data["forecast_index"]) == H
    pd.Timestamp(data["forecast_index"][0])
