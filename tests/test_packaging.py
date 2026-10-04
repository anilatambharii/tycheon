"""Packaging metadata is part of the contract: extras, version and bounds."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

import tycheon

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Extras promised by the T0 brief: the three foundation models, the accelerator
# libraries, the API surface, and the governed workflow (which also pulls
# keelgate from T4).
EXPECTED_EXTRAS = {"kronos", "timesfm", "chronos", "gpu", "serve", "agents"}

# Torch is a multi-gigabyte install. AGENTS.md puts heavy models behind extras,
# so none of these may appear in the base dependency set.
HEAVY_PACKAGES = ("torch", "timesfm", "chronos", "accelerate", "transformers")


@pytest.fixture(scope="module")
def pyproject() -> dict[str, Any]:
    data: dict[str, Any] = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    return data


def test_extras_declared(pyproject: dict[str, Any]) -> None:
    extras = set(pyproject["project"]["optional-dependencies"])
    assert extras == EXPECTED_EXTRAS


def test_every_extra_lists_at_least_one_dependency(pyproject: dict[str, Any]) -> None:
    for name, deps in pyproject["project"]["optional-dependencies"].items():
        assert deps, f"extra {name!r} declares no dependencies"


def test_version_matches_metadata(pyproject: dict[str, Any]) -> None:
    assert tycheon.__version__ == pyproject["project"]["version"]


def test_license_is_apache(pyproject: dict[str, Any]) -> None:
    assert pyproject["project"]["license"] == "Apache-2.0"
    assert (PROJECT_ROOT / "LICENSE").read_text().startswith("Apache License")


def test_requires_python_covers_the_ci_matrix(pyproject: dict[str, Any]) -> None:
    assert pyproject["project"]["requires-python"] == ">=3.11"


def test_core_dependencies_are_upper_bounded(pyproject: dict[str, Any]) -> None:
    """A library pinning only lower bounds breaks downstream resolvers later."""
    for dep in pyproject["project"]["dependencies"]:
        assert "<" in dep, f"dependency {dep!r} has no upper bound"


def test_core_install_stays_light(pyproject: dict[str, Any]) -> None:
    base = " ".join(pyproject["project"]["dependencies"]).lower()
    for heavy in HEAVY_PACKAGES:
        assert heavy not in base, f"{heavy} must live behind an extra, not in core"


def test_yfinance_is_not_a_declared_dependency(pyproject: dict[str, Any]) -> None:
    """yfinance is examples/local-dev only and never ships in the product."""
    project = pyproject["project"]
    declared = list(project["dependencies"])
    for deps in project["optional-dependencies"].values():
        declared.extend(deps)
    assert not [d for d in declared if "yfinance" in d.lower()]
