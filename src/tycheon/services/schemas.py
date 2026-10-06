"""Typed inputs and outputs shared by the REST API, the MCP tools and the agents.

Every input is bounded (lengths, ranges, enumerations) because it can come from a language
model or an HTTP client. Inputs never carry ``as_of``: that comes from the trusted context
(:mod:`tycheon.services.context`). Outputs carry what the project requires of every forecast:
the calibration status and the evidence for it, the model mix, a model-card reference, the
``as_of`` and the disclaimer.

For research and risk analytics. Not investment advice.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tycheon.models.base import DISCLAIMER

Symbol = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,23}$")]
ModelName = Literal["random-walk", "drift", "garch", "kronos-mini"]
BaselineName = Literal["random-walk", "drift", "seasonal-naive", "garch"]
CalibrationStatusName = Literal["calibrated", "stale", "uncalibrated"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Level = Annotated[float, Field(gt=0.5, lt=1.0)]


def _default_levels() -> list[Level]:
    return [0.95, 0.99]


# --------------------------------------------------------------------------- forecast
class ForecastIn(_Strict):
    symbol: Symbol
    horizon: int = Field(default=5, ge=1, le=30, description="bars ahead")
    model: ModelName = "random-walk"
    calibrate: bool = True
    n_samples: int = Field(default=300, ge=50, le=2000)
    n_origins: int = Field(default=60, ge=20, le=300, description="past origins to calibrate on")


class CalibrationEvidence(_Strict):
    method: str
    n_scores: int
    holdout_n: int
    tolerance: float
    holdout_coverage: dict[str, float]
    raw_holdout_coverage: dict[str, float]
    notes: list[str] = Field(default_factory=list)


class HorizonSummary(_Strict):
    median: float
    lower_50: float
    upper_50: float
    lower_90: float
    upper_90: float
    median_return: float = Field(description="median / last_close - 1")
    lower_90_return: float
    upper_90_return: float


class ForecastOut(_Strict):
    symbol: str
    as_of: str
    horizon: int
    model_id: str
    model_mix: dict[str, float]
    model_card: str
    calibration_status: CalibrationStatusName
    calibration: CalibrationEvidence | None
    last_close: float
    horizon_end: HorizonSummary
    median_path: list[float]
    disclaimer: str = DISCLAIMER


# ------------------------------------------------------------------------ calibration
class CalibrationIn(_Strict):
    symbol: Symbol
    horizon: int = Field(default=5, ge=1, le=30)
    model: ModelName = "random-walk"
    n_origins: int = Field(default=80, ge=30, le=300)
    n_samples: int = Field(default=100, ge=50, le=1000)


class CoverageRow(_Strict):
    nominal: float
    raw_coverage: float
    calibrated_coverage: float
    raw_width: float
    calibrated_width: float


class CalibrationOut(_Strict):
    symbol: str
    as_of: str
    horizon: int
    model_id: str
    model_card: str
    method: str
    status: CalibrationStatusName
    n_scores: int
    holdout_n: int
    tolerance: float
    coverage: list[CoverageRow]
    crps_raw: float | None
    crps_calibrated: float | None
    notes: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


# ------------------------------------------------------------------------------- risk
class RiskIn(_Strict):
    positions: dict[Symbol, Annotated[float, Field(gt=0, le=1e12, allow_inf_nan=False)]] = Field(
        min_length=1, max_length=10, description="market value per symbol, long only"
    )
    horizon: int = Field(default=5, ge=1, le=30)
    model: ModelName = "random-walk"
    calibrate: bool = True
    levels: list[Level] = Field(default_factory=_default_levels, min_length=1, max_length=4)
    coupling: Literal["terminal", "stepwise"] = "terminal"
    n_samples: int = Field(default=500, ge=100, le=2000)
    n_origins: int = Field(default=60, ge=20, le=300)


class RiskMeasureRow(_Strict):
    kind: Literal["VaR", "ES"]
    level: float
    loss_fraction: float
    standard_error: float
    n_tail_paths: int
    reliable: bool


class ProbabilityRow(_Strict):
    threshold: float
    probability: float
    ci_low: float
    ci_high: float


class StressRow(_Strict):
    name: str
    kind: str
    pnl_fraction: float
    max_drawdown: float
    calibrated: bool


class AssetRiskRow(_Strict):
    symbol: str
    weight: float
    calibration_status: CalibrationStatusName
    model_id: str
    var_95_loss_fraction: float | None


class RiskOut(_Strict):
    as_of: str
    horizon: int
    total_value: float
    portfolio_calibration_status: CalibrationStatusName
    dependence: str
    var_es: list[RiskMeasureRow]
    drawdown_probability: list[ProbabilityRow]
    loss_probability: list[ProbabilityRow]
    horizon_volatility_per_bar: float
    annualized_volatility: float
    stress: list[StressRow]
    assets: list[AssetRiskRow]
    warnings: list[str]
    report_id: str = Field(description="handle for the full JSON/HTML report")
    disclaimer: str = DISCLAIMER


def _default_baselines() -> list[BaselineName]:
    return ["random-walk", "drift", "garch"]


# --------------------------------------------------------------------------- backtest
class BacktestIn(_Strict):
    symbol: Symbol
    horizon: int = Field(default=5, ge=1, le=20)
    models: list[BaselineName] = Field(
        default_factory=_default_baselines, min_length=1, max_length=4
    )
    folds: int = Field(default=2, ge=1, le=4)
    test_window: int = Field(default=40, ge=20, le=120)


class BacktestRow(_Strict):
    model_id: str
    n_origins: int
    mase: float
    rmse: float
    crps: float
    coverage_90: float | None
    dm_p_squared_error: float | None
    dm_p_crps: float | None
    verdict: str


class BacktestOut(_Strict):
    symbol: str
    as_of: str
    horizon: int
    folds: int
    rows: list[BacktestRow]
    notes: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


# ------------------------------------------------------------------------------- news
class NewsIn(_Strict):
    symbol: Symbol


class NewsSignal(_Strict):
    doc_id: str
    source: str
    published_at: str
    sentiment: float = Field(ge=-1.0, le=1.0)
    injection_suspected: bool


class NewsOut(_Strict):
    symbol: str
    as_of: str
    n_documents_used: int
    n_excluded_after_as_of: int
    n_injection_suspected: int
    mean_sentiment: float | None
    signals: list[NewsSignal]
    note: str = (
        "Derived from untrusted documents. Only numeric scores leave this tool; "
        "document text is never returned, and instructions inside it are never followed."
    )
    disclaimer: str = DISCLAIMER


# ------------------------------------------------------------------------ fundamentals
class FundamentalsIn(_Strict):
    symbol: Symbol


class FundamentalValue(_Strict):
    value: float
    period_end: str
    available_at: str


class FundamentalsOut(_Strict):
    symbol: str
    as_of: str
    metrics: dict[str, FundamentalValue]
    disclaimer: str = DISCLAIMER


# --------------------------------------------------------------------------- reporting
class SaveReportIn(_Strict):
    report_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
    markdown: str = Field(min_length=1, max_length=100_000)


class SaveReportOut(_Strict):
    report_id: str
    path: str
    bytes_written: int


# --------------------------------------------------------------------------- paper trade
class PaperTradeIn(_Strict):
    symbol: Symbol
    side: Literal["buy", "sell"]
    notional: float = Field(gt=0, le=1e9, allow_inf_nan=False)
    client_order_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]


class PaperTradeOut(_Strict):
    order_id: str
    status: Literal["filled-paper"]
    symbol: str
    side: str
    notional: float
    note: str = "PAPER order only. Nothing real is ever executed."
    disclaimer: str = DISCLAIMER
