"""Regression: importing Keelgate's regopy before duckdb corrupts the heap on Linux.

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
