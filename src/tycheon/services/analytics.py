"""The analytics behind the REST API, the MCP tools and the agents.

Each function takes one typed input, reads ``as_of``, the tenant and the data source from the
trusted context (:mod:`tycheon.services.context`), and returns one typed output. They are plain
functions with no Keelgate import: the governance layer wraps them as governed tools, and the
REST layer calls them through that same governed path.

For research and risk analytics. Not investment advice.
"""

from __future__ import annotations

import functools
import hashlib
import math
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from tycheon.backtest import WalkForwardConfig, evaluate, static, walk_forward
from tycheon.calibration import CalibratedForecaster
from tycheon.errors import DataValidationError, TycheonError
from tycheon.models.baselines import (
    DriftForecaster,
    GARCHForecaster,
    RandomWalkForecaster,
    SeasonalNaiveForecaster,
)
from tycheon.risk import Portfolio, build_risk_report, estimate_correlation
from tycheon.services.artifacts import ARTIFACTS
from tycheon.services.context import ServiceError, current
from tycheon.services.schemas import (
    AssetRiskRow,
    BacktestIn,
    BacktestOut,
    BacktestRow,
    CalibrationEvidence,
    CalibrationIn,
    CalibrationOut,
    CoverageRow,
    ForecastIn,
    ForecastOut,
    HorizonSummary,
    ProbabilityRow,
    RiskIn,
    RiskMeasureRow,
    RiskOut,
    StressRow,
)

if TYPE_CHECKING:
    import pandas as pd

    from tycheon.calibration.diagnostics import CalibrationReport
    from tycheon.models.base import ForecastDistribution, Forecaster

#: Longest history handed to a forecaster (bars): bounds cost and keeps results comparable.
MAX_HISTORY = 500
KRONOS_HISTORY = 256


@functools.lru_cache(maxsize=1)
def _kronos_mini() -> Forecaster:
    from tycheon.models.kronos import KronosForecaster  # heavy: only when asked for

    return KronosForecaster(variant="mini")


def make_forecaster(name: str) -> Forecaster:
    """A forecaster by name. ``kronos-mini`` needs the ``kronos`` extra and downloads weights."""
    if name == "random-walk":
        return RandomWalkForecaster()
    if name == "drift":
        return DriftForecaster(window=250)
    if name == "seasonal-naive":
        return SeasonalNaiveForecaster(season_length=5)
    if name == "garch":
        return GARCHForecaster(window=MAX_HISTORY)
    if name == "kronos-mini":
        return _kronos_mini()
    raise ServiceError(f"unknown model {name!r}")


def _history_cap(model: str) -> int:
    return KRONOS_HISTORY if model.startswith("kronos") else MAX_HISTORY


def _key(value: float) -> str:
    return f"{value:g}"


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ServiceError("a computed value was not finite; refusing to report it")
    return float(value)


def _opt(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else float(value)


def _forecast_for(
    symbol: str,
    *,
    horizon: int,
    model: str,
    calibrate: bool,
    n_samples: int,
    n_origins: int,
) -> tuple[ForecastDistribution, CalibrationReport | None, pd.DataFrame]:
    ctx = current()
    try:
        bars = ctx.data.bars(symbol, ctx.as_of)
        inner = make_forecaster(model)
        cap = _history_cap(model)
        history = bars.iloc[-cap:]
        report = None
        if calibrate:
            try:
                calibrated = CalibratedForecaster.fit_on(
                    inner,
                    bars,
                    as_of=ctx.as_of,
                    horizon=horizon,
                    n_origins=n_origins,
                    n_samples=n_samples,
                    max_history=cap,
                )
            except DataValidationError:
                # too little history to replay: fall through to the raw, honestly labelled forecast
                dist = inner.predict(history, horizon, n_samples, ctx.as_of, seed=1)
                return dist, None, bars
            dist = calibrated.predict(history, horizon, n_samples, ctx.as_of, seed=1)
            report = calibrated.report
        else:
            dist = inner.predict(history, horizon, n_samples, ctx.as_of, seed=1)
    except ServiceError:
        raise
    except TycheonError as exc:
        raise ServiceError(f"{type(exc).__name__}: {exc}") from exc
    return dist, report, bars


def _evidence(dist: ForecastDistribution) -> CalibrationEvidence | None:
    info = dist.calibration
    if info is None:
        return None
    return CalibrationEvidence(
        method=info.method,
        n_scores=info.n_scores,
        holdout_n=info.holdout_n,
        tolerance=_finite(info.tolerance),
        holdout_coverage={_key(k): _finite(v) for k, v in sorted(info.holdout_coverage.items())},
        raw_holdout_coverage={
            _key(k): _finite(v) for k, v in sorted(info.raw_holdout_coverage.items())
        },
        notes=list(info.notes),
    )


def run_forecast(args: ForecastIn) -> ForecastOut:
    """A forecast distribution for one symbol, calibrated when the history allows it."""
    ctx = current()
    dist, _, _ = _forecast_for(
        args.symbol,
        horizon=args.horizon,
        model=args.model,
        calibrate=args.calibrate,
        n_samples=args.n_samples,
        n_origins=args.n_origins,
    )
    try:
        lo90, hi90 = dist.interval(0.9)
        lo50, hi50 = dist.interval(0.5)
    except ValueError as exc:
        raise ServiceError("the 50% and 90% intervals are not available for this forecast") from exc
    last = dist.last_close
    end = -1
    median = dist.quantile(0.5)
    return ForecastOut(
        symbol=args.symbol,
        as_of=ctx.as_of.isoformat(),
        horizon=args.horizon,
        model_id=dist.metadata.model_id,
        model_mix=dict(dist.model_mix),
        model_card=dist.model_card,
        calibration_status=dist.calibration_status,
        calibration=_evidence(dist),
        last_close=_finite(last),
        horizon_end=HorizonSummary(
            median=_finite(median[end]),
            lower_50=_finite(lo50[end]),
            upper_50=_finite(hi50[end]),
            lower_90=_finite(lo90[end]),
            upper_90=_finite(hi90[end]),
            median_return=_finite(median[end] / last - 1.0),
            lower_90_return=_finite(lo90[end] / last - 1.0),
            upper_90_return=_finite(hi90[end] / last - 1.0),
        ),
        median_path=[_finite(x) for x in median],
    )


def run_calibration(args: CalibrationIn) -> CalibrationOut:
    """How well calibration worked for a model on this series: raw versus calibrated coverage."""
    ctx = current()
    try:
        bars = ctx.data.bars(args.symbol, ctx.as_of)
        inner = make_forecaster(args.model)
        calibrated = CalibratedForecaster.fit_on(
            inner,
            bars,
            as_of=ctx.as_of,
            horizon=args.horizon,
            n_origins=args.n_origins,
            n_samples=args.n_samples,
            max_history=_history_cap(args.model),
        )
    except ServiceError:
        raise
    except TycheonError as exc:
        raise ServiceError(f"{type(exc).__name__}: {exc}") from exc
    report = calibrated.report
    if report is None:  # pragma: no cover - fit_on always fits
        raise ServiceError("calibration produced no report")
    status, _, tolerance = calibrated.calibrator.status()
    return CalibrationOut(
        symbol=args.symbol,
        as_of=ctx.as_of.isoformat(),
        horizon=args.horizon,
        model_id=inner.model_id,
        model_card=inner.model_card,
        method=report.method,
        status=status,
        n_scores=report.n_scores,
        holdout_n=report.n_holdout,
        tolerance=_finite(tolerance),
        coverage=[
            CoverageRow(
                nominal=c.nominal,
                raw_coverage=_finite(c.raw),
                calibrated_coverage=_finite(c.calibrated),
                raw_width=_finite(c.width_raw),
                calibrated_width=_finite(c.width_calibrated),
            )
            for c in report.coverages
        ],
        crps_raw=_opt(report.crps_raw),
        crps_calibrated=_opt(report.crps_calibrated),
        notes=list(calibrated.calibrator.scores.notes) if calibrated.calibrator.scores else [],
    )


def _has_matplotlib() -> bool:
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        return False
    return True


def run_risk(args: RiskIn) -> RiskOut:
    """VaR, Expected Shortfall, drawdown, volatility and stress for a long-only portfolio.

    Several assets are coupled with an *assumed* correlation, so a multi-asset result is always
    reported ``uncalibrated`` as a whole; the notes and warnings say why.
    """
    ctx = current()
    symbols = list(args.positions)
    forecasts: dict[str, ForecastDistribution] = {}
    reports: dict[str, CalibrationReport] = {}
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        dist, report, bars = _forecast_for(
            symbol,
            horizon=args.horizon,
            model=args.model,
            calibrate=args.calibrate,
            n_samples=args.n_samples,
            n_origins=args.n_origins,
        )
        forecasts[symbol], bars_by_symbol[symbol] = dist, bars
        if report is not None:
            reports[symbol] = report
    try:
        portfolio = Portfolio.from_values(dict(args.positions), name="portfolio")
        correlation = (
            estimate_correlation(bars_by_symbol, as_of=ctx.as_of) if len(symbols) > 1 else None
        )
        built = build_risk_report(
            portfolio=portfolio,
            forecasts=forecasts,
            bars_by_symbol=bars_by_symbol,
            as_of=ctx.as_of,
            calibration_reports=reports,
            correlation=correlation,
            levels=tuple(args.levels),
            coupling=args.coupling,
            with_plots=_has_matplotlib(),
            title="Tycheon risk report",
        )
    except TycheonError as exc:
        raise ServiceError(f"{type(exc).__name__}: {exc}") from exc

    data: dict[str, Any] = built.data
    pr = data["portfolio_risk"]
    report_id = hashlib.sha256(built.to_json().encode()).hexdigest()[:16]
    ARTIFACTS.put(ctx.tenant_id, report_id, built)
    total = float(data["portfolio"]["total_value"])
    weights = {p["symbol"]: float(p["weight"]) for p in data["portfolio"]["positions"]}

    def var95(asset: dict[str, Any]) -> float | None:
        for row in asset["risk"]["var_es"]:
            if row["kind"] == "VaR" and abs(row["level"] - 0.95) < 1e-9:
                return float(row["loss_fraction"])
        return None

    return RiskOut(
        as_of=data["as_of"],
        horizon=int(data["horizon_bars"]),
        total_value=total,
        portfolio_calibration_status=pr["calibration_status"],
        dependence=str(pr["dependence"]),
        var_es=[
            RiskMeasureRow(
                kind=m["kind"],
                level=m["level"],
                loss_fraction=_finite(m["loss_fraction"]),
                standard_error=_finite(m["standard_error"]),
                n_tail_paths=int(m["n_tail_paths"]),
                reliable=bool(m["reliable"]),
            )
            for m in pr["var_es"]
        ],
        drawdown_probability=[_prob(p) for p in pr["drawdown_probability"]],
        loss_probability=[_prob(p) for p in pr["loss_probability"]],
        horizon_volatility_per_bar=_finite(pr["volatility"]["horizon_vol_per_bar"]),
        annualized_volatility=_finite(pr["volatility"]["annualized"]),
        stress=[
            StressRow(
                name=str(s["name"])[:120],
                kind=str(s["kind"]),
                pnl_fraction=_finite(s["pnl_fraction"]),
                max_drawdown=_finite(s["max_drawdown"]),
                calibrated=bool(s["calibrated"]),
            )
            for s in data["stress"]
        ],
        assets=[
            AssetRiskRow(
                symbol=symbol,
                weight=weights[symbol],
                calibration_status=asset["calibration_status"],
                model_id=asset["model_id"],
                var_95_loss_fraction=var95(asset),
            )
            for symbol, asset in data["assets"].items()
        ],
        warnings=[str(w) for w in data["warnings"]],
        report_id=report_id,
    )


def _prob(p: dict[str, Any]) -> ProbabilityRow:
    return ProbabilityRow(
        threshold=p["threshold"],
        probability=_finite(p["probability"]),
        ci_low=_finite(p["ci_low"]),
        ci_high=_finite(p["ci_high"]),
    )


def run_backtest(args: BacktestIn) -> BacktestOut:
    """Walk-forward evaluation of baselines against the random walk, with Diebold-Mariano."""
    ctx = current()
    names = list(dict.fromkeys(["random-walk", *args.models]))
    try:
        bars = ctx.data.bars(args.symbol, ctx.as_of)
        config = WalkForwardConfig(
            horizon=args.horizon,
            folds=args.folds,
            test_window=args.test_window,
            embargo=args.horizon,
            n_samples=100,
            max_history=MAX_HISTORY,
            min_history=60,
        )
        scores = {
            n: replace(walk_forward(static(make_forecaster(n)), bars, config).scores, model_id=n)
            for n in names
        }
        benchmark = scores["random-walk"]
        rows = []
        for n in names:
            ev = evaluate(scores[n], benchmark)
            cov = ev.coverage.get(0.9)
            dm = ev.dm
            rows.append(
                BacktestRow(
                    model_id=n,
                    n_origins=ev.n_origins,
                    mase=_finite(ev.mase),
                    rmse=_finite(ev.rmse),
                    crps=_finite(ev.crps),
                    coverage_90=None if cov is None else _opt(cov[0]),
                    dm_p_squared_error=None
                    if ev.is_benchmark
                    else _opt(dm["squared_error"].p_model_better),
                    dm_p_crps=None if ev.is_benchmark else _opt(dm["crps"].p_model_better),
                    verdict=ev.verdict,
                )
            )
    except ServiceError:
        raise
    except TycheonError as exc:
        raise ServiceError(f"{type(exc).__name__}: {exc}") from exc
    notes = [
        "Verdicts come from one-sided Diebold-Mariano tests against the random walk on this "
        "series only; with few origins they have little power.",
    ]
    if "random-walk" not in args.models:
        notes.append("the random walk was added: every evaluation is read against it")
    return BacktestOut(
        symbol=args.symbol,
        as_of=ctx.as_of.isoformat(),
        horizon=args.horizon,
        folds=args.folds,
        rows=rows,
        notes=notes,
    )
