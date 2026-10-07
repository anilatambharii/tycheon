"""Fixtures for the Tycheon Cloud tests.

Row-level security cannot be tested on SQLite or a mock, so tests marked ``postgres`` need a real
Postgres: set ``TYCHEON_TEST_DATABASE_URL`` (the owner role, which runs migrations) and
``TYCHEON_TEST_APP_URL`` (the unprivileged application role). CI provides both from a service
container; without them these tests are skipped and say so, they do not pass silently.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import asyncpg

# duckdb must be imported before Keelgate's regopy (heap corruption on Linux otherwise); see
# src/tycheon/governance/__init__.py.
import duckdb  # noqa: F401
import pytest

from tycheon_cp.db import Database, migrate

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

APP_ROLE = "tycheon_app"
WEBHOOK_SECRET = "whsec_test_only_not_a_real_secret_0000"  # pragma: allowlist secret
OPERATOR_TOKEN = "operator-token-for-tests-0123456789abcdef"  # pragma: allowlist secret


@dataclass(frozen=True)
class PgUrls:
    owner: str
    app: str


def _urls() -> PgUrls | None:
    owner, app = os.environ.get("TYCHEON_TEST_DATABASE_URL"), os.environ.get("TYCHEON_TEST_APP_URL")
    return PgUrls(owner, app) if owner and app else None


async def _reset_and_migrate(urls: PgUrls) -> None:
    conn = await asyncpg.connect(urls.owner)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()
    await migrate(urls.owner, app_role=APP_ROLE)


@pytest.fixture(scope="session")
def pg() -> PgUrls:
    urls = _urls()
    if urls is None:
        pytest.skip("set TYCHEON_TEST_DATABASE_URL and TYCHEON_TEST_APP_URL (a real Postgres)")
    asyncio.run(_reset_and_migrate(urls))
    return urls


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def db(pg: PgUrls) -> AsyncIterator[Database]:
    database = await Database.connect(pg.app, max_size=8)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def owner(pg: PgUrls) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(pg.owner)
    try:
        yield conn
    finally:
        await conn.close()


# ------------------------------------------------------------------------ the whole API
def synthetic_csv(
    n: int = 400, *, seed: int = 7, start: str = "2023-01-02", base: float = 100.0, freq: str = "B"
) -> str:
    """A believable bars CSV: geometric random walk, OHLC consistent, with volume."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    close = base * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    open_ = np.concatenate([[base], close[:-1]])
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range(start, periods=n, freq=freq),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(1_000, 50_000, n).astype(float),
        }
    )
    return frame.to_csv(index=False)


@pytest.fixture(scope="session")
def runtime():
    from tycheon.governance import GovernedRuntime

    return GovernedRuntime()


@pytest.fixture
async def cp(pg: PgUrls, db: Database, runtime, tmp_path):
    import base64

    from tycheon_cp.app import build_control_plane
    from tycheon_cp.config import Settings
    from tycheon_cp.ratelimit import RateLimiter

    settings = Settings(
        database_url=pg.app,
        session_secret="s" * 40,
        storage_root=tmp_path / "blobs",
        env="test",
        local_kms_key=base64.b64encode(os.urandom(32)).decode(),
        stripe_webhook_secret=WEBHOOK_SECRET,
        operator_token=OPERATOR_TOKEN,
    )
    return await build_control_plane(settings, db=db, runtime=runtime, limiter=RateLimiter())


@pytest.fixture
async def client(cp, fake_stripe):
    import httpx

    from tycheon_cp.factory import create_full_app

    app = create_full_app(cp, stripe_api=fake_stripe)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://cp.test") as http:
        http.app = app  # type: ignore[attr-defined]
        yield http


class Tenant:
    """A signed-up organisation, as an API client sees it."""

    def __init__(self, client, org_id: str, user_id: str, token: str, email: str) -> None:
        self.client, self.org_id, self.user_id, self.token, self.email = (
            client,
            org_id,
            user_id,
            token,
            email,
        )
        self.api_key: str | None = None

    @property
    def session(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def key(self) -> dict[str, str]:
        assert self.api_key, "call make_key() first"
        return {"X-API-Key": self.api_key}

    async def make_key(self, scopes=("analytics",)) -> str:
        res = await self.client.post(
            "/v1/api-keys", json={"name": "ci", "scopes": list(scopes)}, headers=self.session
        )
        assert res.status_code == 201, res.text
        self.api_key = res.json()["key"]
        return self.api_key

    async def upload(self, symbol: str, csv: str, source_id: str | None = None) -> dict:
        if source_id is None:
            res = await self.client.post(
                "/v1/data-sources",
                json={"name": f"src-{uuid.uuid4().hex[:6]}", "default": True},
                headers=self.session,
            )
            assert res.status_code == 201, res.text
            source_id = res.json()["id"]
        res = await self.client.put(
            f"/v1/data-sources/{source_id}/files/{symbol}", content=csv, headers=self.session
        )
        assert res.status_code == 200, res.text
        return {"source_id": source_id, **res.json()}


async def sign_up(client, name: str = "Acme Capital") -> Tenant:
    tag = uuid.uuid4().hex[:10]
    email = f"owner-{tag}@example.com"
    res = await client.post(
        "/auth/signup",
        json={"org_name": f"{name} {tag}", "email": email, "password": "correct horse battery"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    return Tenant(client, body["org_id"], body["user_id"], body["token"], email)


@pytest.fixture
def signup_org(client):
    async def make(name: str = "Acme Capital") -> Tenant:
        return await sign_up(client, name)

    return make


async def set_plan(owner_conn, org_id: str, plan: str, override: dict | None = None) -> None:
    import json

    await owner_conn.execute(
        "UPDATE subscriptions SET plan = $2, status = 'active', limits_override = $3::jsonb "
        "WHERE org_id = $1::uuid",
        org_id,
        plan,
        json.dumps(override or {}),
    )
    await owner_conn.execute("UPDATE orgs SET plan = $2 WHERE id = $1::uuid", org_id, plan)


@pytest.fixture
def csv_factory():
    return synthetic_csv


@pytest.fixture
def set_org_plan(owner):
    async def apply(org_id: str, plan: str, override: dict | None = None) -> None:
        await set_plan(owner, org_id, plan, override)

    return apply


class FakeStripe:
    """A stand-in for :class:`tycheon_cp.billing.RealStripe` that records what it is asked."""

    def __init__(self) -> None:
        self.customers: list[dict] = []
        self.checkouts: list[dict] = []
        self.usage: list[dict] = []
        self.invoices: list[dict] = []
        self.catalog_calls = 0

    def ensure_catalog(self, plans):
        self.catalog_calls += 1
        return {p.price.lookup_key: f"price_test_{p.key}" for p in plans.plans.values() if p.price}

    def create_customer(self, *, email, name, org_id):
        self.customers.append({"email": email, "name": name, "org_id": org_id})
        return f"cus_test_{uuid.uuid4().hex[:14]}"

    def checkout_url(self, **kw):
        self.checkouts.append(kw)
        return f"https://checkout.stripe.test/c/{kw['plan']}"

    def portal_url(self, *, customer, return_url):
        return f"https://billing.stripe.test/p/{customer}"

    def preview(self, *, customer, subscription, price_id):
        return {
            "currency": "usd",
            "total_cents": 100_000,
            "period_end": 1_900_000_000,
            "lines": [{"description": "Tycheon Startup", "amount_cents": 100_000}],
            "for": {"customer": customer, "subscription": subscription, "price": price_id},
        }

    def report_usage(self, *, kind, customer, value, identifier, timestamp):
        self.usage.append(
            {"kind": kind, "customer": customer, "value": value, "identifier": identifier}
        )

    def draft_invoice(self, *, customer, description, amount_cents, currency):
        self.invoices.append(
            {"customer": customer, "description": description, "amount_cents": amount_cents}
        )
        return f"in_test_{uuid.uuid4().hex[:14]}"


@pytest.fixture
def fake_stripe() -> FakeStripe:
    return FakeStripe()
