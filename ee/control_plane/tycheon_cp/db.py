"""Postgres access with row-level security.

The only way application code reaches a tenant table is :meth:`Database.tenant`, which opens a
transaction and sets ``app.org_id`` for that transaction alone (``set_config(..., true)``), so
the setting cannot outlive the request or leak through the pool. :meth:`Database.anonymous` has
no org: with RLS on, the only things it can do are call the pre-tenant lookup functions.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from importlib import resources
from typing import TYPE_CHECKING, Any
from uuid import UUID

import asyncpg

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_ROLE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class Database:
    """A connection pool whose connections are always scoped to one tenant (or to none)."""

    def __init__(
        self,
        pool: asyncpg.Pool[Any] | None = None,
        *,
        dsn: str | None = None,
        max_size: int = 10,
    ) -> None:
        if pool is None and dsn is None:
            raise ValueError("a Database needs a pool or a DSN")
        self._pool = pool
        self._dsn, self._max_size = dsn, max_size
        self._lock = asyncio.Lock()

    @classmethod
    async def connect(cls, dsn: str, *, min_size: int = 1, max_size: int = 10) -> Database:
        pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
        if pool is None:  # pragma: no cover - asyncpg returns None only for a closed pool
            raise RuntimeError("could not create the Postgres pool")
        return cls(pool)

    @classmethod
    def lazy(cls, dsn: str, *, max_size: int = 10) -> Database:
        """A database whose pool is opened on first use, inside whichever event loop uses it."""
        return cls(dsn=dsn, max_size=max_size)

    async def _ready(self) -> asyncpg.Pool[Any]:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(self._dsn, max_size=self._max_size)
        if self._pool is None:  # pragma: no cover
            raise RuntimeError("could not create the Postgres pool")
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @contextlib.asynccontextmanager
    async def tenant(self, org_id: UUID | str) -> AsyncIterator[asyncpg.Connection[Any]]:
        """A transaction in which row-level security shows exactly ``org_id``'s rows."""
        org = str(UUID(str(org_id)))  # refuses anything that is not a UUID
        pool = await self._ready()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.org_id', $1, true)", org)
            yield conn

    @contextlib.asynccontextmanager
    async def anonymous(self) -> AsyncIterator[asyncpg.Connection[Any]]:
        """A transaction with no tenant: RLS hides every tenant row from it."""
        pool = await self._ready()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.org_id', '', true)")
            yield conn


_DOWN = ".down.sql"


def _scripts() -> tuple[dict[str, str], dict[str, str]]:
    """``(up, down)`` migration scripts, both keyed by the up migration file name."""
    folder = resources.files("tycheon_cp").joinpath("migrations")
    up: dict[str, str] = {}
    down: dict[str, str] = {}
    for entry in sorted(folder.iterdir(), key=lambda p: p.name):
        if entry.name.endswith(_DOWN):
            down[entry.name.removesuffix(_DOWN) + ".sql"] = entry.read_text(encoding="utf-8")
        elif entry.name.endswith(".sql"):
            up[entry.name] = entry.read_text(encoding="utf-8")
    return up, down


def _check_role(app_role: str) -> None:
    if not _ROLE.fullmatch(app_role):
        raise ValueError("the application role is not a plain lower-case identifier")


async def migrate(owner_dsn: str, *, app_role: str, target: str | None = None) -> list[str]:
    """Apply pending migrations as the owner role; return the versions applied.

    ``app_role`` is the role the application connects as. It is granted exactly what the
    migrations grant and nothing else, and it must not be a superuser or have BYPASSRLS.
    ``target`` stops after that migration (used to prove a rollback reproduces an earlier schema).
    """
    _check_role(app_role)
    up, _ = _scripts()
    if target is not None and target not in up:
        raise ValueError(f"unknown migration {target!r}")
    conn = await asyncpg.connect(owner_dsn)
    applied: list[str] = []
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
        for name, script in up.items():
            if name not in done:
                async with conn.transaction():
                    await conn.execute(script.replace("{app_role}", app_role))
                    await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", name)
                applied.append(name)
            if name == target:
                break
        flags = await conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = $1", app_role
        )
        if flags is None or flags["rolsuper"] or flags["rolbypassrls"]:
            raise RuntimeError(
                f"role {app_role!r} must exist and be neither a superuser nor BYPASSRLS, "
                "or row-level security would not apply to the application"
            )
    finally:
        await conn.close()
    return applied


class RollbackRefusedError(RuntimeError):
    """A rollback that would destroy data was requested without saying so."""


async def rollback(
    owner_dsn: str, *, app_role: str, steps: int = 1, allow_data_loss: bool = False
) -> list[str]:
    """Undo the last ``steps`` applied migrations, newest first; return the versions undone.

    Each undo runs in one transaction together with the removal of its ``schema_migrations`` row,
    so a failure leaves the database exactly as it was. A script that drops tables is refused
    unless ``allow_data_loss`` is set: for a database with data, the recovery is a restore.
    """
    _check_role(app_role)
    if steps < 1:
        raise ValueError("steps must be at least 1")
    _, down = _scripts()
    conn = await asyncpg.connect(owner_dsn)
    undone: list[str] = []
    try:
        rows = await conn.fetch(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT $1", steps
        )
        plan = [r["version"] for r in rows]
        for name in plan:
            if name not in down:
                raise RollbackRefusedError(f"{name} has no down migration")
            if re.search(r"\bDROP\s+TABLE\b", down[name], re.IGNORECASE) and not allow_data_loss:
                raise RollbackRefusedError(
                    f"undoing {name} drops tables and all their data; "
                    "restore from a backup, or pass allow_data_loss for an empty database"
                )
        for name in plan:
            async with conn.transaction():
                await conn.execute(down[name].replace("{app_role}", app_role))
                await conn.execute("DELETE FROM schema_migrations WHERE version = $1", name)
            undone.append(name)
    finally:
        await conn.close()
    return undone
