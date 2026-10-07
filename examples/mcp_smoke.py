"""Smoke-test the Tycheon MCP server over stdio: ``initialize`` then ``tools/list``.

    uv run --extra serve python examples/mcp_smoke.py

This is the same handshake Claude Desktop or Cursor performs when it launches ``tycheon-mcp``.
The script starts the real console script as a subprocess, speaks MCP to it with the official
``mcp`` client SDK, prints what the server says about itself and the tools it exposes, and exits
non-zero if either call fails. It calls no tool, so no forecast is produced and no data is read.

Needs the ``serve`` extra (``mcp`` and Keelgate). Use ``--command`` to test another launcher,
for example ``--command "uv run --extra serve tycheon-mcp"``.

For research and risk analytics. Not investment advice.
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DISCLAIMER = "For research and risk analytics. Not investment advice."


def default_command() -> list[str]:
    """The ``tycheon-mcp`` console script of the interpreter running this script."""
    found = shutil.which("tycheon-mcp")
    if found:
        return [found]
    scripts = Path(sys.executable).parent
    for name in ("tycheon-mcp.exe", "tycheon-mcp"):
        if (scripts / name).is_file():
            return [str(scripts / name)]
    raise SystemExit("tycheon-mcp not found: run `uv sync --extra serve` first, or pass --command")


async def smoke(command: list[str], state_dir: Path) -> int:
    params = StdioServerParameters(
        command=command[0], args=[*command[1:], "--state-dir", str(state_dir)]
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        print(f"initialize ok: server={init.server_info.name} {init.server_info.version}")
        print(f"  protocol version: {init.protocol_version}")
        listing = await session.list_tools()
        print(f"tools/list ok: {len(listing.tools)} tools")
        for tool in listing.tools:
            first_line = (tool.description or "").strip().splitlines()[:1]
            print(f"  - {tool.name}: {first_line[0] if first_line else ''}")
    return 0 if listing.tools else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--command", default=None, help="launcher to test (default: tycheon-mcp)")
    args = parser.parse_args()
    command = shlex.split(args.command) if args.command else default_command()
    print(DISCLAIMER)
    with tempfile.TemporaryDirectory() as tmp:
        code = asyncio.run(smoke(command, Path(tmp)))
    print(DISCLAIMER)
    return code


if __name__ == "__main__":
    sys.exit(main())
