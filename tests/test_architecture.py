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

# Phases T0-T3 were a pure library. From T4 Keelgate may appear, but only in the `agents` and
# `serve` extras, bounded to the 0.1 series, and never in the base install.
KEELGATE_ALLOWED_IN_DEPENDENCIES = True
KEELGATE_EXTRAS = {"agents", "serve"}


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


def test_keelgate_is_only_a_bounded_optional_dependency(pyproject: dict[str, Any]) -> None:
    """The base install never needs the harness; only agents and serve do, pinned to 0.1.x."""
    project = pyproject["project"]
    assert not [d for d in project["dependencies"] if "keelgate" in d.lower()]
    for group, deps in pyproject.get("dependency-groups", {}).items():
        found = [d for d in deps if isinstance(d, str) and "keelgate" in d.lower()]
        assert not found, f"dependency group {group!r} must not require keelgate: {found}"

    declared = {
        name: [d for d in deps if "keelgate" in d.lower()]
        for name, deps in project["optional-dependencies"].items()
    }
    holders = {name for name, found in declared.items() if found}
    if KEELGATE_ALLOWED_IN_DEPENDENCIES:
        assert holders == KEELGATE_EXTRAS, (
            f"keelgate must be in exactly {KEELGATE_EXTRAS}: {holders}"
        )
        for name in holders:
            assert all(">=0.1,<0.2" in d.replace(" ", "") for d in declared[name]), declared[name]
    else:
        assert not holders


# Heavy or optional packages. Importing any of these at module level would make
# `import tycheon.models` require an extra the base install does not have.
OPTIONAL_HEAVY = {"torch", "timesfm", "chronos", "transformers", "accelerate", "einops"}


def _module_level_imports(path: Path) -> set[str]:
    """Roots of imports executed when the module loads (not inside functions or TYPE_CHECKING)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_optional_heavy_packages_are_only_imported_lazily() -> None:
    """The base install must import without torch, TimesFM or Chronos (tested for real in
    test_kronos_vendor.py by importing in a clean subprocess)."""
    offenders = {
        str(path.relative_to(PROJECT_ROOT)): sorted(_module_level_imports(path) & OPTIONAL_HEAVY)
        for path in _python_files()
        if _module_level_imports(path) & OPTIONAL_HEAVY
    }
    assert not offenders, f"optional dependencies imported at module level: {offenders}"


def test_yfinance_is_only_imported_lazily_by_its_own_provider() -> None:
    importers = [
        str(path.relative_to(PROJECT_ROOT))
        for path in _python_files()
        if "yfinance" in _module_level_imports(path)
    ]
    assert not importers, f"yfinance must never be a module-level import: {importers}"


def test_the_vendored_tree_is_loaded_by_path_never_imported_by_name() -> None:
    """third_party/kronos is excluded from the package namespace on purpose."""
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in _python_files()
        if _imported_roots(path) & {"third_party", "model"}
    ]
    assert not offenders, f"import vendored code through tycheon.models.kronos.vendor: {offenders}"


def test_governance_and_agents_are_documented_packages() -> None:
    """Both packages explain themselves and state the rules they live under."""
    for package in ("governance", "agents"):
        readme = SRC / package / "README.md"
        assert readme.is_file() and len(readme.read_text(encoding="utf-8")) > 400, readme
        assert (SRC / package / "__init__.py").is_file()


def _imports_keelgate(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Import) and any(
            a.name.split(".")[0] == "keelgate" for a in child.names
        ):
            return True
        if isinstance(child, ast.ImportFrom) and (child.module or "").split(".")[0] == "keelgate":
            return True
    return False


def test_there_is_no_governance_bypass_fallback_when_keelgate_is_missing() -> None:
    """AGENTS.md: never write a fallback like "if keelgate is missing, execute anyway"."""
    offenders = []
    for path in sorted(GOVERNANCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try) or not any(_imports_keelgate(s) for s in node.body):
                continue
            for handler in node.handlers:
                caught = ast.dump(handler.type) if handler.type is not None else "bare"
                if any(
                    n in caught for n in ("ImportError", "ModuleNotFoundError", "Exception", "bare")
                ):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert not offenders, f"keelgate imports must fail loudly, not fall back: {offenders}"


RAW_SERVICE_CALLS = {
    "run_forecast", "run_calibration", "run_risk", "run_backtest", "news_signals",
}  # fmt: skip


def test_serve_and_agents_reach_the_analytics_only_through_governance() -> None:
    """The structural half of "no bypass": nothing outside governance calls the raw services.

    The governed tools wrap these functions; the REST API and the agents must call the tools
    (grant, policy, audit), never the functions underneath.
    """
    offenders = []
    for package in ("serve", "agents"):
        for path in sorted((SRC / package).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "tycheon.services"
                ):
                    names = {a.name for a in node.names}
                    if names & RAW_SERVICE_CALLS:
                        offenders.append(
                            f"{path.relative_to(PROJECT_ROOT)}: {sorted(names & RAW_SERVICE_CALLS)}"
                        )
    assert not offenders, f"call governed tools, not the raw services: {offenders}"


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
