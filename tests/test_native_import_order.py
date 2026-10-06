"""Regression: DuckDB and Keelgate's regopy corrupt each other's heap on Linux.

CI crashed with exit code 134 (and no output) because pytest auto-loaded Keelgate's plugin,
which imports ``regopy`` before anything imported ``duckdb``. These tests pin the two defences
and prove, in a fresh interpreter, that Tycheon's own import path survives.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed arguments: the interpreter running this test
        [sys.executable, "-X", "faulthandler", "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_importing_the_governance_package_first_does_not_crash() -> None:
    """The exact crash from CI: a fresh process whose first import is tycheon.governance."""
    done = run_python("import tycheon.governance; print('ok')")
    assert done.returncode == 0 and "ok" in done.stdout, done.stderr[-2000:]


def test_the_governance_package_loads_duckdb_before_keelgate() -> None:
    """Whatever order the caller imports in, duckdb is already loaded when Keelgate is."""
    done = run_python(
        "import sys, tycheon.governance;"
        "d = list(sys.modules).index('duckdb'); k = list(sys.modules).index('keelgate');"
        "print('duckdb first' if d < k else 'keelgate first')"
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert "duckdb first" in done.stdout


def test_the_serve_entry_points_survive_a_fresh_import() -> None:
    done = run_python("import tycheon.serve.cli; print('ok')")
    assert done.returncode == 0 and "ok" in done.stdout, done.stderr[-2000:]


def test_pytest_does_not_auto_load_keelgates_plugin() -> None:
    options = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "pytest"
    ]["ini_options"]["addopts"]
    assert "no:keelgate" in options and options[options.index("no:keelgate") - 1] == "-p"


def test_the_governance_source_keeps_the_duckdb_import_ahead_of_the_keelgate_imports() -> None:
    text = (ROOT / "src" / "tycheon" / "governance" / "__init__.py").read_text(encoding="utf-8")
    assert text.index("import duckdb") < text.index("from tycheon.governance._llm import")
    assert "double free" in text  # the reason is written next to the import


def test_a_policy_decision_and_duckdb_coexist_in_one_process() -> None:
    """The real conflict: use DuckDB and evaluate a Rego policy in the same interpreter."""
    program = chr(10).join(
        [
            "import asyncio, duckdb",
            "from datetime import UTC, datetime",
            "from keelgate.policy import PolicyAction, PolicyActor, PolicyInput",
            "from tycheon.governance._policy import TycheonPolicy, policy_context",
            "con = duckdb.connect(); con.execute('select 1').fetchall()",
            "pi = PolicyInput(",
            "    action=PolicyAction(tool='t', side_effect='WRITE',",
            "                        capability='trade:paper_execute', args={}),",
            "    actor=PolicyActor(agent_id='a', tenant_id='acme', grant_id='g'),",
            "    resource={'symbol': 'SYN-GBM', 'notional': 1.0},",
            "    context=policy_context(datetime(2024, 3, 5, 15, tzinfo=UTC)))",
            "print(asyncio.run(TycheonPolicy().decide(pi)).effect.value)",
            "print(con.execute('select 2').fetchall())",
        ]
    )
    done = run_python(program)
    assert done.returncode == 0, done.stderr[-2000:]
    assert "REQUIRE_APPROVAL" in done.stdout and "[(2,)]" in done.stdout
