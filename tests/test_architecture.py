"""Architectural invariants from AGENTS.md, enforced from the first commit.

The rule that matters most here is the Keelgate boundary. Tycheon depends on
the Keelgate harness from Phase T4, and every one of those imports must live in
``src/tycheon/governance/`` so a contract change touches exactly one package.
A boundary tested only once it is crossed is not a boundary.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src" / "tycheon"
GOVERNANCE = SRC / "governance"

# Phases T0-T3 are a pure library: no harness anywhere. In T4 this flips to
# allow `keelgate>=0.1,<0.2` in the `agents` extra only, and the import rule
# below starts to matter for real.
KEELGATE_ALLOWED_IN_DEPENDENCIES = False


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    """Top-level package names imported by a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.fixture(scope="module")
def pyproject() -> dict[str, Any]:
    data: dict[str, Any] = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    return data


def test_source_tree_is_not_empty() -> None:
    """Guard the guards: an empty glob would make every check below vacuous."""
    assert len(_python_files()) >= 10


def test_only_governance_may_import_keelgate() -> None:
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in _python_files()
        if "keelgate" in _imported_roots(path) and GOVERNANCE not in path.parents
    ]
    assert not offenders, (
        f"keelgate may only be imported from src/tycheon/governance/; offenders: {offenders}"
    )


def test_nothing_imports_keelgate_internals() -> None:
    """Only the documented integration contract is fair game."""
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in _python_files()
        if "keelgate._internal" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"keelgate._internal is off limits; offenders: {offenders}"


def test_no_keelgate_dependency_before_phase_t4(pyproject: dict[str, Any]) -> None:
    """T0-T3 build forecasting, calibration, risk and evaluation as a pure library."""
    project = pyproject["project"]
    declared = list(project["dependencies"])
    for deps in project["optional-dependencies"].values():
        declared.extend(deps)
    for deps in pyproject.get("dependency-groups", {}).values():
        declared.extend(d for d in deps if isinstance(d, str))

    found = [d for d in declared if "keelgate" in d.lower()]
    if KEELGATE_ALLOWED_IN_DEPENDENCIES:
        assert found, "T4 onward must pin keelgate explicitly"
    else:
        assert not found, f"phases T0-T3 must not depend on keelgate; found {found}"


def test_governance_and_agents_are_declared_placeholders() -> None:
    """Both placeholders explain themselves, so nobody fills them in early."""
    for package in ("governance", "agents"):
        readme = SRC / package / "README.md"
        text = readme.read_text(encoding="utf-8")
        assert "T4" in text, f"{readme} must say when it arrives"
        assert (SRC / package / "__init__.py").is_file()


def test_governance_readme_forbids_the_bypass() -> None:
    """The one failure mode that must never ship: degrading past the harness."""
    text = (GOVERNANCE / "README.md").read_text(encoding="utf-8").lower()
    assert "fails closed" in text or "fail closed" in text
    assert "bypass" in text


def test_no_module_shadows_a_third_party_package() -> None:
    """A top-level `data.py` or `models.py` on sys.path would shadow real ones."""
    assert not (PROJECT_ROOT / "src" / "data").exists()
    assert not (PROJECT_ROOT / "src" / "models").exists()


def test_nothing_in_src_imports_the_proprietary_ee_tree() -> None:
    """The dependency arrow points inward only: Cloud depends on OSS, never the reverse.

    ADR 0001 rests on this. Delete ee/ and the open product must still work.
    """
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in _python_files()
        if "ee" in _imported_roots(path)
    ]
    assert not offenders, f"src/ must not import from ee/; offenders: {offenders}"
