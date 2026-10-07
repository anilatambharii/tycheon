"""Operational commands: ``python -m tycheon_cp.cli <command>``.

* ``migrate``      apply database migrations (owner connection: ``TYCHEON_CP_MIGRATION_URL``)
* ``serve``        run the API (``--factory`` target ``tycheon_cp.cli:app_factory`` for uvicorn)
* ``report-usage`` send unreported usage to Stripe meters (run on a schedule)
* ``purge``        apply per-plan retention (run on a schedule)
* ``ensure-catalog`` create the Stripe products, prices and meters from ``plans.toml`` (test mode)

Configuration is environment variables only (see ``Settings.from_env``); nothing is stored.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
import time
from typing import TYPE_CHECKING, Any

import asyncpg

# DuckDB first: it cannot share a process with Keelgate's regopy once both are in use
# (src/tycheon/governance/__init__.py explains).
import duckdb  # noqa: F401

from tycheon_cp.config import ConfigError, Settings
from tycheon_cp.db import RollbackRefusedError
from tycheon_cp.db import migrate as run_migrations
from tycheon_cp.db import rollback as run_rollback

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


def app_factory() -> FastAPI:
    """The full application for ``uvicorn --factory tycheon_cp.cli:app_factory``."""
    from tycheon_cp.app import make_control_plane  # noqa: PLC0415
    from tycheon_cp.db import Database  # noqa: PLC0415
    from tycheon_cp.factory import create_full_app  # noqa: PLC0415
    from tycheon_ft.loader import PrivateModelResolver  # noqa: PLC0415
    from tycheon_ft.providers import PinnedKronos  # noqa: PLC0415

    settings = Settings.from_env()
    plane = make_control_plane(settings, db=Database.lazy(settings.database_url))
    plane.gateway.private_models = PrivateModelResolver(
        plane.db, plane.blobs, PinnedKronos(os.environ.get("TYCHEON_CP_KRONOS", "mini"))
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await plane.db.close()

    return create_full_app(plane, lifespan=lifespan)


async def _with_plane(action: Any) -> Any:
    from tycheon_cp.app import build_control_plane  # noqa: PLC0415

    plane = await build_control_plane(Settings.from_env())
    try:
        return await action(plane)
    finally:
        await plane.db.close()


async def _migrate_with_wait(url: str, role: str, wait: float) -> list[str]:
    """Run migrations, retrying the connection for up to ``wait`` seconds (a database starting)."""
    deadline = time.monotonic() + wait
    while True:
        try:
            return await run_migrations(url, app_role=role)
        except (OSError, asyncpg.CannotConnectNowError, asyncpg.ConnectionDoesNotExistError) as exc:
            if time.monotonic() >= deadline:
                raise
            print(f"database not ready ({type(exc).__name__}); retrying", file=sys.stderr)  # noqa: T201
            await asyncio.sleep(3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tycheon_cp", description="Tycheon Cloud operations")
    sub = parser.add_subparsers(dest="command", required=True)
    migrate = sub.add_parser("migrate")
    migrate.add_argument(
        "--wait", type=float, default=0.0, help="retry connecting for this many seconds"
    )
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    sub.add_parser("report-usage")
    sub.add_parser("purge")
    sub.add_parser("ensure-catalog")
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)  # noqa: T201
        return 2


def _run(args: argparse.Namespace) -> int:  # noqa: PLR0911 - one return per sub-command
    if args.command == "migrate":
        url = os.environ.get("TYCHEON_CP_MIGRATION_URL")
        if not url:
            raise ConfigError("TYCHEON_CP_MIGRATION_URL is required (the owner role)")
        role = os.environ.get("TYCHEON_CP_APP_ROLE", "tycheon_app")
        applied = asyncio.run(_migrate_with_wait(url, role, args.wait))
        print("applied:", ", ".join(applied) or "nothing to do")  # noqa: T201
        return 0
    if args.command == "rollback":
        url = os.environ.get("TYCHEON_CP_MIGRATION_URL")
        if not url:
            raise ConfigError("TYCHEON_CP_MIGRATION_URL is required (the owner role)")
        role = os.environ.get("TYCHEON_CP_APP_ROLE", "tycheon_app")
        try:
            undone = asyncio.run(
                run_rollback(
                    url, app_role=role, steps=args.steps, allow_data_loss=args.allow_data_loss
                )
            )
        except RollbackRefusedError as exc:
            print(f"refused: {exc}", file=sys.stderr)  # noqa: T201
            return 3
        print("rolled back:", ", ".join(undone) or "nothing")  # noqa: T201
        return 0
    if args.command == "serve":
        import uvicorn  # noqa: PLC0415

        if args.host not in ("127.0.0.1", "localhost", "::1"):
            print("binding a non-loopback address: put TLS in front of this", file=sys.stderr)  # noqa: T201
        uvicorn.run("tycheon_cp.cli:app_factory", factory=True, host=args.host, port=args.port)
        return 0
    if args.command == "report-usage":
        from tycheon_cp.billing_routes import build_billing  # noqa: PLC0415

        async def report(plane: Any) -> int:
            return await build_billing(plane).report_usage()

        print("reported:", asyncio.run(_with_plane(report)))  # noqa: T201
        return 0
    if args.command == "purge":
        from tycheon_cp.retention import purge  # noqa: PLC0415

        async def run(plane: Any) -> dict[str, int]:
            return await purge(plane.db, plane.plans, plane.blobs)

        print("purged:", asyncio.run(_with_plane(run)))  # noqa: T201
        return 0
    if args.command == "ensure-catalog":
        from tycheon_cp.billing_routes import build_billing  # noqa: PLC0415

        async def catalog(plane: Any) -> dict[str, str]:
            return await build_billing(plane).prices()

        print("prices:", asyncio.run(_with_plane(catalog)))  # noqa: T201
        return 0
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
