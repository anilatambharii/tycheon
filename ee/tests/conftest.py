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
