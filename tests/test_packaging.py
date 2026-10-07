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
EXPECTED_EXTRAS = {"kronos", "timesfm", "chronos", "gpu", "serve", "report", "agents"}

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


def test_the_kronos_extra_carries_what_the_vendored_code_imports(pyproject: dict[str, Any]) -> None:
    """Upstream imports torch, einops and tqdm at module load and loads weights via the Hub."""
    deps = " ".join(pyproject["project"]["optional-dependencies"]["kronos"]).lower()
    for needed in ("torch", "einops", "tqdm", "huggingface-hub", "safetensors"):
        assert needed in deps, f"the kronos extra is missing {needed}"


def test_the_foundation_model_extras_pull_the_right_packages(pyproject: dict[str, Any]) -> None:
    extras = pyproject["project"]["optional-dependencies"]
    assert any("timesfm[torch]" in d for d in extras["timesfm"]), "TimesFM needs its torch backend"
    assert any(d.startswith("chronos-forecasting>=2") for d in extras["chronos"]), (
        "Chronos-2 is 2.x"
    )


def test_keelgate_is_only_in_the_agents_and_serve_extras(pyproject: dict[str, Any]) -> None:
    holders = {
        name
        for name, deps in pyproject["project"]["optional-dependencies"].items()
        if any("keelgate" in d.lower() for d in deps)
    }
    assert holders == {"agents", "serve"}


def test_the_vendored_kronos_licence_ships_with_the_distribution(pyproject: dict[str, Any]) -> None:
    """Kronos is MIT; Apache-2.0 redistribution must carry its notice (AGENTS.md)."""
    assert "third_party/kronos/LICENSE" in pyproject["project"]["license-files"]
    force = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force == {"third_party/kronos": "tycheon/_vendor/kronos"}


def test_cpu_torch_is_pinned_for_linux_dev_and_ci_only(pyproject: dict[str, Any]) -> None:
    """Keeps CI from downloading ~3 GB of CUDA libraries; published metadata is unaffected."""
    sources = pyproject["tool"]["uv"]["sources"]["torch"]
    assert sources == [{"index": "pytorch-cpu", "marker": "sys_platform == 'linux'"}]
    (index,) = pyproject["tool"]["uv"]["index"]
    assert index["explicit"] is True and index["url"].endswith("/whl/cpu")
    assert "tool" not in pyproject["project"]


def test_yfinance_is_not_a_declared_dependency(pyproject: dict[str, Any]) -> None:
    """yfinance is examples/local-dev only and never ships in the product."""
    project = pyproject["project"]
    declared = list(project["dependencies"])
    for deps in project["optional-dependencies"].values():
        declared.extend(deps)
    assert not [d for d in declared if "yfinance" in d.lower()]
    groups = [d for deps in pyproject.get("dependency-groups", {}).values() for d in deps]
    assert not [d for d in groups if isinstance(d, str) and "yfinance" in d.lower()]
