"""Every declared module must import cleanly.

Phase T0 ships empty modules, so this is the whole of its functional surface:
if the layout drifts from AGENTS.md, this fails first.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest

import tycheon

# The layout AGENTS.md prescribes for src/tycheon/.
CORE_MODULES = [
    "tycheon.backtest",
    "tycheon.calibration",
    "tycheon.covariates",
    "tycheon.data",
    "tycheon.models",
    "tycheon.risk",
    "tycheon.routing",
    "tycheon.serve",
]

MODEL_MODULES = [
    "tycheon.models.baselines",
    "tycheon.models.chronos",
    "tycheon.models.kronos",
    "tycheon.models.timesfm",
]

# Placeholders until Phase T4. They must import today so the boundary they
# define is real before there is code to put behind it.
T4_MODULES = [
    "tycheon.agents",
    "tycheon.governance",
]


def test_top_level_import() -> None:
    assert tycheon.__version__
    assert tycheon.__doc__ is not None


@pytest.mark.parametrize("name", CORE_MODULES)
def test_core_module_imports(name: str) -> None:
    assert importlib.import_module(name) is not None


@pytest.mark.parametrize("name", MODEL_MODULES)
def test_model_module_imports(name: str) -> None:
    assert importlib.import_module(name) is not None


@pytest.mark.parametrize("name", T4_MODULES)
def test_placeholder_module_imports(name: str) -> None:
    """A placeholder still has to be importable without its T4 dependencies."""
    assert importlib.import_module(name) is not None


def test_no_module_in_tree_fails_to_import() -> None:
    """Walk the installed package so a new broken module cannot slip through."""
    failures: list[str] = []
    for info in pkgutil.walk_packages(tycheon.__path__, prefix="tycheon."):
        try:
            importlib.import_module(info.name)
        except Exception as exc:  # we want to report every failure, not the first
            failures.append(f"{info.name}: {exc!r}")
    assert not failures, "modules failed to import: " + "; ".join(failures)


def test_every_module_in_tree_is_declared() -> None:
    """The tree and the declared layout must not drift apart."""
    found = {info.name for info in pkgutil.walk_packages(tycheon.__path__, prefix="tycheon.")}
    declared = set(CORE_MODULES) | set(MODEL_MODULES) | set(T4_MODULES)
    assert found == declared, (
        f"undeclared modules: {sorted(found - declared)}; "
        f"missing from tree: {sorted(declared - found)}"
    )


def test_every_module_has_a_docstring() -> None:
    """Empty modules are allowed; undocumented ones are not."""
    undocumented = [
        info.name
        for info in pkgutil.walk_packages(tycheon.__path__, prefix="tycheon.")
        if not (importlib.import_module(info.name).__doc__ or "").strip()
    ]
    assert not undocumented, f"modules without a docstring: {undocumented}"


def test_package_is_typed() -> None:
    """py.typed must ship, or downstream `mypy --strict` sees Tycheon as Any."""
    assert (Path(next(iter(tycheon.__path__))) / "py.typed").is_file()
