"""Command-line entry points: ``tycheon-serve`` (REST) and ``tycheon-mcp`` (MCP over stdio).

Both build the same governed runtime. Neither stores a secret: API keys and approver tokens are
read from the environment (``TYCHEON_API_KEYS``, ``TYCHEON_APPROVER_TOKENS``), and the server
refuses to start without keys unless ``--insecure-dev`` is given, which in turn refuses any
address but loopback.

Examples::

    tycheon-serve --port 8080               # with tenant:key pairs set in the environment
    tycheon-serve --insecure-dev            # local only: no authentication, 127.0.0.1 only
    tycheon-mcp                             # an MCP client launches this and speaks over stdio
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tycheon.data.sample import sample_end
from tycheon.governance import (
    ApproverToken,
    GovernedRuntime,
    RuntimeConfig,
    build_approvals_app,
    build_mcp,
)
from tycheon.serve.auth import ApiKeyError, ApiKeys
from tycheon.services import DataSource, build_sample_fundamentals, sample_news

_LOOPBACK = {"localhost"}


def _is_loopback(host: str) -> bool:
    if host in _LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", type=Path, default=None, help="where state is kept")
    parser.add_argument("--tenant", default="default", help="the tenant for MCP / insecure-dev")
    parser.add_argument("--as-of", default=None, help="fix the server's as_of (ISO, with offset)")
    parser.add_argument("--data-dir", type=Path, default=None, help="your own licensed CSV bars")


def _runtime(args: argparse.Namespace) -> tuple[GovernedRuntime, datetime]:
    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of)
        if as_of.tzinfo is None:
            raise SystemExit(
                "--as-of must include a UTC offset, for example 2023-10-02T14:30:00+00:00"
            )
    elif args.data_dir is None:
        as_of = sample_end("SYN-GBM")  # the bundled series end here; "now" would be misleading
    else:
        as_of = datetime.now(UTC)
    state = args.state_dir
    if args.data_dir is not None:
        data = DataSource(kind="files", path=args.data_dir)
    else:
        root = (state or Path(".tycheon-state")) / "fundamentals"
        data = DataSource(news=sample_news(), fundamentals=build_sample_fundamentals(root))
    config = RuntimeConfig(state_dir=state, tenant_id=args.tenant, data=data, default_as_of=as_of)
    return GovernedRuntime(config), as_of


def _approver_tokens(value: str | None) -> dict[str, ApproverToken]:
    if not value:
        return {}
    raw: dict[str, Any] = json.loads(value)
    return {
        token: ApproverToken(
            approver_id=str(entry["approver_id"]),
            tenant_id=str(entry["tenant_id"]),
            max_tier=str(entry.get("max_tier", "ONE_CLICK")),
        )
        for token, entry in raw.items()
    }


def serve_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tycheon-serve", description="Tycheon REST API")
    _common(parser)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--insecure-dev", action="store_true", help="no authentication; loopback only"
    )
    args = parser.parse_args(argv)

    try:
        keys = ApiKeys.parse(os.environ.get("TYCHEON_API_KEYS"))
    except ApiKeyError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    if not keys and not args.insecure_dev:
        sys.stderr.write(
            "error: set TYCHEON_API_KEYS (tenant:key pairs, keys of 16+ characters), or pass "
            "--insecure-dev for local development\n"
        )
        return 2
    if args.insecure_dev and not _is_loopback(args.host):
        sys.stderr.write("error: --insecure-dev only works on a loopback address\n")
        return 2

    import uvicorn  # imported here so `--help` works without the serve extra's server stack

    from tycheon.serve.app import create_app

    runtime, as_of = _runtime(args)
    tokens = _approver_tokens(os.environ.get("TYCHEON_APPROVER_TOKENS"))
    approvals = build_approvals_app(runtime, tokens) if tokens else None
    app = create_app(
        runtime,
        keys,
        as_of=lambda: as_of,
        approvals_app=approvals,
        insecure_dev_tenant=args.tenant if args.insecure_dev and not keys else None,
    )
    try:
        uvicorn.run(app, host=args.host, port=args.port, access_log=False)
    finally:
        runtime.close()
    return 0


def mcp_main(argv: list[str] | None = None) -> int:
    """Serve the governed tools over MCP on stdio (stdout is the protocol; logs go to stderr)."""
    parser = argparse.ArgumentParser(prog="tycheon-mcp", description="Tycheon MCP server")
    _common(parser)
    args = parser.parse_args(argv)
    runtime, as_of = _runtime(args)
    try:
        handle = build_mcp(runtime, as_of=as_of, tenant_id=args.tenant)
        asyncio.run(handle.serve_stdio())
    finally:
        runtime.close()
    return 0
