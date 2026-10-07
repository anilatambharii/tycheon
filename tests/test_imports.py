"""Every declared module must import cleanly, and the package layout must not drift.

The packages below are the layout AGENTS.md prescribes. Adding or removing a package
is a deliberate change that must show up in this list; leaf modules inside a package
are free to come and go, but every one of them must import and be documented.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest

import tycheon

# Top-level packages from the AGENTS.md layout, plus the shared errors module.
CORE_PACKAGES = [
    "tycheon.backtest",
    "tycheon.calibration",
    "tycheon.covariates",
    "tycheon.data",
    "tycheon.models",
    "tycheon.risk",
    "tycheon.routing",
    "tycheon.serve",
]

DATA_PACKAGES = ["tycheon.data.providers"]

MODEL_PACKAGES = [
    "tycheon.models.baselines",
    "tycheon.models.chronos",
    "tycheon.models.kronos",
    "tycheon.models.timesfm",
]

# Phase T4: the governed agentic workflow. `governance` is the only package that imports Keelgate;
# `services` is the typed analytics layer both it and the REST API use; `agents` is pure Python.
T4_PACKAGES = [
    "tycheon.agents",
    "tycheon.governance",
    "tycheon.services",
]

DECLARED_PACKAGES = CORE_PACKAGES + DATA_PACKAGES + MODEL_PACKAGES + T4_PACKAGES


def _walk() -> list[pkgutil.ModuleInfo]:
    return list(pkgutil.walk_packages(tycheon.__path__, prefix="tycheon."))


def test_top_level_import() -> None:
    assert tycheon.__version__
    assert tycheon.__doc__ is not None


@pytest.mark.parametrize("name", DECLARED_PACKAGES)
def test_declared_package_imports(name: str) -> None:
    assert importlib.import_module(name) is not None


def test_no_module_in_tree_fails_to_import() -> None:
    """Walk the installed package so a new broken module cannot slip through."""
    failures: list[str] = []
    for info in _walk():
        try:
            importlib.import_module(info.name)
        except Exception as exc:  # we want to report every failure, not the first
            failures.append(f"{info.name}: {exc!r}")
    assert not failures, "modules failed to import: " + "; ".join(failures)


def test_the_set_of_packages_matches_the_declared_layout() -> None:
    """The package tree and the declared layout must not drift apart."""
    found = {info.name for info in _walk() if info.ispkg}
    declared = set(DECLARED_PACKAGES)
    assert found == declared, (
        f"undeclared packages: {sorted(found - declared)}; "
        f"missing from tree: {sorted(declared - found)}"
    )


def test_every_module_has_a_docstring() -> None:
    """Empty modules are allowed; undocumented ones are not."""
    undocumented = [
        info.name
        for info in _walk()
        if not (importlib.import_module(info.name).__doc__ or "").strip()
    ]
    assert not undocumented, f"modules without a docstring: {undocumented}"


def test_package_is_typed() -> None:
    """py.typed must ship, or downstream `mypy --strict` sees Tycheon as Any."""
    assert (Path(next(iter(tycheon.__path__))) / "py.typed").is_file()
