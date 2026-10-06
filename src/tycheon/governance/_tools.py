"""Tycheon's governed tools: the only way anything in Tycheon reaches a side effect or an analytic.

Each tool wraps one typed service function (:mod:`tycheon.services`) with a Keelgate declaration:
the capability it needs, whether it can have side effects, a timeout and a cost. Keelgate's
gateway is the only thing that can run one (grant, policy, approval, audit first); a ``Tool`` is
not callable directly.

Two details that matter:

* the tool bodies read ``as_of``, the tenant and the data source from the *trusted context* the
  runtime binds, never from their arguments (a model cannot choose the date);
* ``propose_paper_trade`` and ``save_report`` are ``WRITE`` tools, so each needs an idempotency
  key, and the trade can only run after a human approval (see :mod:`._policy`).
"""

from __future__ import annotations

import re
from pathlib import Path

from keelgate.tools import SideEffect, Tool, ToolRefusedError, tool

from tycheon.services import (
    PaperBlotter,
    current,
    news_signals,
    run_backtest,
    run_calibration,
    run_forecast,
    run_risk,
)
from tycheon.services.context import ServiceError
from tycheon.services.schemas import (
    BacktestIn,
    BacktestOut,
    CalibrationIn,
    CalibrationOut,
    ForecastIn,
    ForecastOut,
    FundamentalsIn,
    FundamentalsOut,
    NewsIn,
    NewsOut,
    PaperTradeIn,
    PaperTradeOut,
    RiskIn,
    RiskOut,
    SaveReportIn,
    SaveReportOut,
)

_SAFE_TENANT = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def build_tools(*, blotter: PaperBlotter, report_dir: Path) -> list[Tool]:
    """The governed tool set, bound to a paper blotter and a report directory."""

    @tool(
        capability="forecast:run",
        side_effect=SideEffect.READ,
        name="forecast_distribution",
        timeout_s=180.0,
        cost_estimate=1.0,
    )
    def forecast_distribution(args: ForecastIn) -> ForecastOut:
        """Forecast distribution for one symbol, calibrated when history allows."""
        return run_forecast(args)

    @tool(
        capability="calibration:run",
        side_effect=SideEffect.READ,
        name="calibration_report",
        timeout_s=240.0,
        cost_estimate=2.0,
    )
    def calibration_report(args: CalibrationIn) -> CalibrationOut:
        """Raw versus calibrated interval coverage for a model on one series."""
        return run_calibration(args)

    @tool(
        capability="risk:compute",
        side_effect=SideEffect.READ,
        name="portfolio_risk",
        timeout_s=300.0,
        cost_estimate=3.0,
    )
    def portfolio_risk(args: RiskIn) -> RiskOut:
        """VaR, Expected Shortfall, drawdown, volatility and stress for a long-only portfolio."""
        return run_risk(args)

    @tool(
        capability="backtest:run",
        side_effect=SideEffect.READ,
        name="backtest_summary",
        timeout_s=300.0,
        cost_estimate=2.0,
    )
    def backtest_summary(args: BacktestIn) -> BacktestOut:
        """Walk-forward evaluation of baselines against the random walk."""
        return run_backtest(args)

    @tool(
        capability="news:read",
        side_effect=SideEffect.READ,
        name="news_signals",
        timeout_s=30.0,
        cost_estimate=0.2,
    )
    def news_signals_tool(args: NewsIn) -> NewsOut:
        """Numeric sentiment signals from untrusted news published by the review date."""
        ctx = current()
        if ctx.data.news is None:
            raise ServiceError("no news source is configured")
        return news_signals(args.symbol, ctx.data.news, ctx.as_of)

    @tool(
        capability="fundamentals:read",
        side_effect=SideEffect.READ,
        name="fundamentals_snapshot",
        timeout_s=30.0,
        cost_estimate=0.2,
    )
    def fundamentals_snapshot(args: FundamentalsIn) -> FundamentalsOut:
        """Latest fundamentals published by the review date (restatements do not leak)."""
        ctx = current()
        if ctx.data.fundamentals is None:
            raise ServiceError("no fundamentals source is configured")
        return ctx.data.fundamentals.snapshot(args.symbol, ctx.as_of)

    @tool(
        capability="report:write",
        side_effect=SideEffect.WRITE,
        name="save_report",
        timeout_s=20.0,
        cost_estimate=0.5,
        idempotency_key=lambda a: a.report_id,
    )
    def save_report(args: SaveReportIn) -> SaveReportOut:
        """Save a verified report as Markdown under the tenant's report directory."""
        tenant = current().tenant_id
        if not _SAFE_TENANT.fullmatch(tenant):
            raise ToolRefusedError("the tenant id is not safe to use as a directory name")
        root = report_dir.resolve()
        target = (root / tenant / f"{args.report_id}.md").resolve()
        if root not in target.parents:
            raise ToolRefusedError("the report path escapes the report directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        data = args.markdown.encode("utf-8")
        target.write_bytes(data)
        return SaveReportOut(
            report_id=args.report_id,
            path=str(Path(tenant) / f"{args.report_id}.md"),
            bytes_written=len(data),
        )

    @tool(
        capability="trade:paper_execute",
        side_effect=SideEffect.WRITE,
        name="propose_paper_trade",
        timeout_s=20.0,
        cost_estimate=2.0,
        idempotency_key=lambda a: a.client_order_id,
        resource=lambda a: {"symbol": a.symbol, "notional": a.notional},
    )
    def propose_paper_trade(args: PaperTradeIn) -> PaperTradeOut:
        """Propose a PAPER trade; it runs only after a human approves it. Never real money."""
        return blotter.place(current().tenant_id, args)

    return [
        forecast_distribution,
        calibration_report,
        portfolio_risk,
        backtest_summary,
        news_signals_tool,
        fundamentals_snapshot,
        save_report,
        propose_paper_trade,
    ]
