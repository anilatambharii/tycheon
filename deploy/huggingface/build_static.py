"""Precompute the demo's results with the real Tycheon services.

A static Space has no server, so every number the page shows is computed here, once, over a fixed
grid of the bundled synthetic series, and written to ``data.json``. Nothing is invented: each entry
is the response of the same ``/v1/forecast`` or ``/v1/risk`` call a user of the API would make.
"""

from __future__ import annotations

import json
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tycheon.data.sample import sample_end
from tycheon.governance import GovernedRuntime, RuntimeConfig
from tycheon.serve import ApiKeys, create_app

SYMBOLS = ("SYN-GBM", "SYN-GARCH", "SYN-REGIME", "SYN-JUMP")
MODELS = ("random-walk", "drift", "garch")
FORECAST_HORIZONS = (1, 5, 10, 20)
RISK_HORIZONS = (5, 10, 20)
POSITION = 100_000.0
KEY = "static-build-key-0000000000"  # pragma: allowlist secret
DISCLAIMER = "For research and risk analytics. Not investment advice."


def _key(symbol: str, model: str, horizon: int, calibrated: bool) -> str:
    return f"{symbol}|{model}|{horizon}|{'cal' if calibrated else 'raw'}"


def _forecast_entry(out: dict[str, Any]) -> dict[str, Any]:
    keep = ("as_of", "horizon", "model_id", "calibration_status", "last_close", "horizon_end")
    return {k: out[k] for k in keep}


def _risk_entry(out: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "horizon",
        "portfolio_calibration_status",
        "annualized_volatility",
        "var_es",
        "warnings",
    )
    return {k: out[k] for k in keep}


def build(
    symbols: tuple[str, ...] = SYMBOLS,
    models: tuple[str, ...] = MODELS,
    forecast_horizons: tuple[int, ...] = FORECAST_HORIZONS,
    risk_horizons: tuple[int, ...] = RISK_HORIZONS,
) -> dict[str, Any]:
    as_of = sample_end("SYN-GBM")
    with tempfile.TemporaryDirectory() as tmp:
        runtime = GovernedRuntime(RuntimeConfig(state_dir=Path(tmp), tenant_id="static"))
        try:
            app = create_app(runtime, ApiKeys({KEY: "static"}), as_of=lambda: as_of)
            headers = {"Authorization": f"Bearer {KEY}"}
            forecast: dict[str, Any] = {}
            risk: dict[str, Any] = {}
            with TestClient(app) as client:
                for symbol in symbols:
                    for model in models:
                        for calibrated in (True, False):
                            for h in forecast_horizons:
                                body = {
                                    "symbol": symbol,
                                    "model": model,
                                    "horizon": h,
                                    "calibrate": calibrated,
                                }
                                r = client.post("/v1/forecast", json=body, headers=headers)
                                r.raise_for_status()
                                forecast[_key(symbol, model, h, calibrated)] = _forecast_entry(
                                    r.json()
                                )
                            for h in risk_horizons:
                                body = {
                                    "positions": {symbol: POSITION},
                                    "model": model,
                                    "horizon": h,
                                    "calibrate": calibrated,
                                    "n_samples": 1000,
                                }
                                r = client.post("/v1/risk", json=body, headers=headers)
                                r.raise_for_status()
                                risk[_key(symbol, model, h, calibrated)] = _risk_entry(r.json())
        finally:
            runtime.close()
    return {
        "tycheon_version": version("tycheon"),
        "as_of": as_of.isoformat(),
        "symbols": list(symbols),
        "models": list(models),
        "forecast_horizons": list(forecast_horizons),
        "risk_horizons": list(risk_horizons),
        "forecast": forecast,
        "risk": risk,
        "disclaimer": DISCLAIMER,
    }


def write(path: Path) -> dict[str, Any]:
    data = build()
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return data
