"""The public front door of the Hugging Face demo Space: what it forwards and what it refuses."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy" / "huggingface" / "app"))
import proxy

if TYPE_CHECKING:
    from collections.abc import Iterator

KEY = "k" * 32


def _upstream(
    seen: list[httpx.Request], status: int = 200, body: Any = None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"ok": True} if body is None else body)

    return httpx.MockTransport(handler)


@pytest.fixture
def seen() -> list[httpx.Request]:
    return []


@pytest.fixture
def client(seen: list[httpx.Request]) -> Iterator[TestClient]:
    with TestClient(proxy.create_app(KEY, transport=_upstream(seen))) as c:
        yield c


def test_forecast_is_forwarded_with_the_internal_key_and_the_disclaimer(
    client: TestClient, seen: list[httpx.Request]
) -> None:
    r = client.post("/api/forecast", json={"symbol": "SYN-GBM", "horizon": 7})
    assert r.status_code == 200
    assert r.json()["disclaimer"] == proxy.DISCLAIMER
    assert seen[0].url.path == "/v1/forecast"
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    sent = json.loads(seen[0].content)
    assert sent["horizon"] == 7 and sent["n_samples"] == 300


@pytest.mark.parametrize(
    "body",
    [
        {"symbol": "AAPL"},
        {"symbol": "SYN-GBM", "model": "kronos-mini"},
        {"symbol": "SYN-GBM", "model": "ft:other-tenant"},
        {"symbol": "SYN-GBM", "horizon": 500},
        {"symbol": "SYN-GBM", "horizon": True},
        {"symbol": ["SYN-GBM"]},
    ],
)
def test_forecast_refuses_anything_outside_the_allowlist(
    client: TestClient, seen: list[httpx.Request], body: dict[str, Any]
) -> None:
    assert client.post("/api/forecast", json=body).status_code == 422
    assert seen == []


def test_risk_caps_positions_and_values(client: TestClient, seen: list[httpx.Request]) -> None:
    ok = client.post("/api/risk", json={"positions": {"SYN-GBM": 1000}})
    assert ok.status_code == 200
    too_many = dict.fromkeys(("SYN-GBM", "SYN-GARCH", "SYN-REGIME", "SYN-JUMP", "X1", "X2"), 1.0)
    for bad in (
        {"positions": too_many},
        {"positions": {"AAPL": 1000}},
        {"positions": {"SYN-GBM": -5}},
        {"positions": {"SYN-GBM": 1e15}},
        {"positions": {}},
    ):
        assert client.post("/api/risk", json=bad).status_code == 422
    assert len(seen) == 1


def test_oversized_and_malformed_bodies_never_reach_the_api(
    client: TestClient, seen: list[httpx.Request]
) -> None:
    big = {"symbol": "SYN-GBM", "pad": "x" * 3000}
    assert client.post("/api/forecast", json=big).status_code == 413
    assert client.post("/api/forecast", content=b"not json").status_code == 422
    assert client.post("/api/forecast", json=[1, 2]).status_code == 422
    assert seen == []


def test_rate_limit_is_per_client(client: TestClient) -> None:
    body = {"symbol": "SYN-GBM"}
    codes = [
        client.post("/api/forecast", json=body, headers={"x-forwarded-for": "1.1.1.1"}).status_code
        for _ in range(proxy.RATE_LIMIT + 2)
    ]
    assert codes[: proxy.RATE_LIMIT] == [200] * proxy.RATE_LIMIT
    assert codes[proxy.RATE_LIMIT :] == [429, 429]
    other = client.post("/api/forecast", json=body, headers={"x-forwarded-for": "2.2.2.2"})
    assert other.status_code == 200


def test_rate_window_slides() -> None:
    limiter = proxy.RateLimiter(2, 10.0)
    assert limiter.allow("a", 0.0) and limiter.allow("a", 1.0)
    assert not limiter.allow("a", 2.0)
    assert limiter.allow("a", 11.5)


def test_upstream_failure_is_a_bad_gateway_without_internals() -> None:
    seen: list[httpx.Request] = []
    app = proxy.create_app(KEY, transport=_upstream(seen, 500, {"detail": "secret trace"}))
    with TestClient(app) as c:
        r = c.post("/api/forecast", json={"symbol": "SYN-GBM"})
    assert r.status_code == 502
    assert "secret" not in r.text


def test_api_rejection_is_passed_on_briefly() -> None:
    seen: list[httpx.Request] = []
    app = proxy.create_app(KEY, transport=_upstream(seen, 422, {"detail": "x" * 1000}))
    with TestClient(app) as c:
        r = c.post("/api/forecast", json={"symbol": "SYN-GBM"})
    assert r.status_code == 422
    assert len(r.json()["error"]) <= 300


def test_only_the_demo_surface_is_exposed(client: TestClient) -> None:
    assert client.get("/").status_code == 200
    assert "Not investment advice" in client.get("/").text
    for path in ("/docs", "/openapi.json", "/v1/forecast", "/v1/jobs"):
        assert client.get(path).status_code in {404, 405}
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/").headers["x-content-type-options"] == "nosniff"


def test_every_series_the_page_offers_exists_in_the_sample_data() -> None:
    from tycheon.data.sample import SAMPLE_DATASETS

    assert set(SAMPLE_DATASETS) >= proxy.SYMBOLS
