"""Forecast-to-risk: VaR, Expected Shortfall, drawdown, volatility, stress and portfolios.

Everything is computed from joint sample paths and carries the forecast's calibration status,
so a number is never presented as more trustworthy than the forecast under it.
"""

from tycheon.risk.measures import (
    DEFAULT_DRAWDOWNS,
    DEFAULT_LEVELS,
    ProbabilityEstimate,
    RiskInput,
    RiskMeasure,
    VolatilityForecast,
    drawdown_probabilities,
    loss_probabilities,
    max_drawdown,
    value_at_risk_and_es,
    volatility_forecast,
)
from tycheon.risk.portfolio import (
    Portfolio,
    PortfolioForecast,
    Position,
    aggregate_joint_paths,
    aggregate_portfolio,
    couple_paths,
    estimate_correlation,
    stress_correlation,
)
from tycheon.risk.report import RiskReport, build_risk_report
from tycheon.risk.scenarios import (
    ScenarioResult,
    historical_replay,
    model_implied_tail,
    run_stress_suite,
    shock,
    worst_windows,
)

__all__ = [
    "DEFAULT_DRAWDOWNS",
    "DEFAULT_LEVELS",
    "Portfolio",
    "PortfolioForecast",
    "Position",
    "ProbabilityEstimate",
    "RiskInput",
    "RiskMeasure",
    "RiskReport",
    "ScenarioResult",
    "VolatilityForecast",
    "aggregate_joint_paths",
    "aggregate_portfolio",
    "build_risk_report",
    "couple_paths",
    "drawdown_probabilities",
    "estimate_correlation",
    "historical_replay",
    "loss_probabilities",
    "max_drawdown",
    "model_implied_tail",
    "run_stress_suite",
    "shock",
    "stress_correlation",
    "value_at_risk_and_es",
    "volatility_forecast",
    "worst_windows",
]
