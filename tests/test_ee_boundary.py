"""The open-core boundary (ADR 0001), enforced: the dependency only ever points inward.

* nothing in ``src/tycheon`` may import the proprietary ``ee/`` code;
* ``ee/`` reaches Keelgate only through ``tycheon.governance`` (AGENTS.md: one integration point);
* the published OSS package and source distribution do not contain ``ee/``;
* every proprietary source file says so.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "tycheon"
EE = ROOT / "ee"
EE_PACKAGES = ("tycheon_cp", "tycheon_ft")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def python_files(base: Path) -> list[Path]:
    return [
        p for p in base.rglob("*.py") if "node_modules" not in p.parts and ".next" not in p.parts
    ]


def test_the_open_source_package_never_imports_the_proprietary_code() -> None:
    offenders = []
    for path in python_files(SRC):
        for module in imported_modules(path):
            root = module.split(".")[0]
            if root in {"ee", *EE_PACKAGES}:
                offenders.append(f"{path.relative_to(ROOT)} imports {module}")
    assert not offenders, offenders


def test_the_open_source_package_does_not_even_name_the_proprietary_packages_in_strings() -> None:
    for path in python_files(SRC):
        text = path.read_text(encoding="utf-8")
        for name in EE_PACKAGES:
            assert name not in text.replace("tycheon.governance", ""), f"{path} mentions {name}"


def test_the_proprietary_code_reaches_keelgate_only_through_the_governance_package() -> None:
    offenders = []
    for path in python_files(EE):
        for module in imported_modules(path):
            if module.split(".")[0] == "keelgate":
                offenders.append(f"{path.relative_to(ROOT)} imports {module}")
    assert not offenders, offenders


def test_the_proprietary_code_never_imports_keelgates_internals_or_a_bypass() -> None:
    for path in python_files(EE):
        text = path.read_text(encoding="utf-8")
        assert "keelgate._internal" not in text and "keelgate/_internal" not in text, path
        assert "TYCHEON_SKIP_GOVERNANCE" not in text and "bypass_governance" not in text


def test_the_published_wheel_and_sdist_do_not_contain_ee() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = project["tool"]["hatch"]["build"]["targets"]["wheel"]
    sdist = project["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert wheel["packages"] == ["src/tycheon"]
    assert all(not str(p).startswith(("ee", "./ee")) for p in sdist["include"])
    assert all(str(p).split("/")[0] != "ee" for p in wheel.get("force-include", {}))


def test_every_proprietary_source_file_carries_the_licence_notice() -> None:
    missing = []
    for base in (EE / "control_plane", EE / "finetune"):
        for path in python_files(base):
            if path.name == "__init__.py" and path.stat().st_size == 0:
                continue
            if "Proprietary: see ee/LICENSE" not in path.read_text(encoding="utf-8"):
                missing.append(str(path.relative_to(ROOT)))
    assert not missing, missing


def test_the_cloud_dependencies_are_only_in_the_ee_extras() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    base = " ".join(project["project"]["dependencies"]).lower()
    for name in ("asyncpg", "stripe", "pyjwt", "cryptography"):
        assert name not in base
    extras = project["project"]["optional-dependencies"]
    assert any("asyncpg" in d for d in extras["ee"]) and any("boto3" in d for d in extras["ee-aws"])
    for oss_extra in ("serve", "agents", "report", "kronos"):
        assert not any(
            n in " ".join(extras.get(oss_extra, [])).lower() for n in ("asyncpg", "stripe")
        )
