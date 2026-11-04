"""Public front door for the Tycheon demo Space.

The real Tycheon API runs on loopback inside the container with a per-boot random key. This app is
the only thing exposed: it serves one static page and forwards a fixed allowlist of calls, so a
visitor cannot choose a data source, a model that needs weights, or an endpoint that costs more
than the demo budget. Data is the synthetic series bundled with Tycheon; nothing here reads or
stores user data.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

DISCLAIMER = "For research and risk analytics. Not investment advice."
UPSTREAM = os.environ.get("TYCHEON_UPSTREAM", "http://127.0.0.1:8080")
SYMBOLS = frozenset({"SYN-GBM", "SYN-GARCH", "SYN-REGIME", "SYN-JUMP"})
MODELS = frozenset({"random-walk", "drift", "garch"})
MAX_BODY = 2_000
MAX_POSITIONS = 5
RATE_LIMIT = 12  # calls per client per window
RATE_WINDOW = 60.0
MAX_CONCURRENT = 2
TIMEOUT = 90.0
STATIC = Path(__file__).parent / "static"


def _check_choice(value: Any, allowed: frozenset[str], what: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise HTTPException(422, f"{what} must be one of {sorted(allowed)}")
    return value


def _clamp(value: Any, low: int, high: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise HTTPException(422, f"{what} must be an integer from {low} to {high}")
    return value


def forecast_body(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": _check_choice(raw.get("symbol"), SYMBOLS, "symbol"),
        "model": _check_choice(raw.get("model", "random-walk"), MODELS, "model"),
        "horizon": _clamp(raw.get("horizon", 5), 1, 20, "horizon"),
        "calibrate": bool(raw.get("calibrate", True)),
        "n_samples": 300,
        "n_origins": 60,
    }


def risk_body(raw: dict[str, Any]) -> dict[str, Any]:
    positions = raw.get("positions")
    if not isinstance(positions, dict) or not 1 <= len(positions) <= MAX_POSITIONS:
        raise HTTPException(422, f"positions must hold 1 to {MAX_POSITIONS} symbols")
    clean: dict[str, float] = {}
    for symbol, value in positions.items():
        _check_choice(symbol, SYMBOLS, "position symbol")
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not 0 < value <= 1_000_000_000
        ):
            raise HTTPException(422, "each position value must be a number above 0")
        clean[symbol] = float(value)
    return {
        "positions": clean,
        "model": _check_choice(raw.get("model", "random-walk"), MODELS, "model"),
        "horizon": _clamp(raw.get("horizon", 5), 1, 20, "horizon"),
        "calibrate": bool(raw.get("calibrate", True)),
        "n_samples": 1000,
        "n_origins": 60,
    }


class RateLimiter:
    """Sliding-window limit per client address; in memory, so a restart resets it."""

    def __init__(self, limit: int, window: float) -> None:
        self._limit = limit
        self._window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, client: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        hits = self._hits[client]
        while hits and now - hits[0] > self._window:
            hits.popleft()
        if not hits:
            self._hits.pop(client, None)
            hits = self._hits[client]
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True


def client_of(request: Request) -> str:
    """The visitor address. The Space sits behind Hugging Face's proxy, which sets this header."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


def create_app(
    api_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    key = api_key or os.environ["TYCHEON_DEMO_KEY"]
    app = FastAPI(title="Tycheon demo", docs_url=None, redoc_url=None, openapi_url=None)
    limiter = RateLimiter(RATE_LIMIT, RATE_WINDOW)
    slots = asyncio.Semaphore(MAX_CONCURRENT)

    @app.middleware("http")
    async def headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    async def forward(request: Request, path: str, build: Any) -> JSONResponse:
        if not limiter.allow(client_of(request)):
            raise HTTPException(429, "Too many requests. Try again in a minute.")
        body = await request.body()
        if len(body) > MAX_BODY:
            raise HTTPException(413, "request too large")
        try:
            raw = await request.json()
        except ValueError as exc:
            raise HTTPException(422, "body must be JSON") from exc
        if not isinstance(raw, dict):
            raise HTTPException(422, "body must be a JSON object")
        payload = build(raw)
        if slots.locked():
            raise HTTPException(503, "The demo is busy. Try again in a few seconds.")
        async with (
            slots,
            httpx.AsyncClient(base_url=UPSTREAM, timeout=TIMEOUT, transport=transport) as client,
        ):
            try:
                reply = await client.post(
                    path, json=payload, headers={"Authorization": f"Bearer {key}"}
                )
            except httpx.HTTPError as exc:
                raise HTTPException(502, "the analytics service did not answer") from exc
        if reply.status_code >= 500:
            raise HTTPException(502, "the analytics service failed")
        data = reply.json()
        if reply.status_code >= 400:
            detail = data.get("detail", "request rejected") if isinstance(data, dict) else ""
            return JSONResponse({"error": str(detail)[:300], "disclaimer": DISCLAIMER}, 422)
        data["disclaimer"] = DISCLAIMER
        return JSONResponse(data)

    @app.post("/api/forecast")
    async def forecast(request: Request) -> JSONResponse:
        return await forward(request, "/v1/forecast", forecast_body)

    @app.post("/api/risk")
    async def risk(request: Request) -> JSONResponse:
        return await forward(request, "/v1/risk", risk_body)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html")

    return app
