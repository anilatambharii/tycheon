"""The Makefile is the documented entrypoint, so its targets are part of the contract.

This cannot catch a shell-level typo inside a recipe -- the `make check` job in
CI is what does that -- but it does catch a target that was renamed or dropped,
and it pins what `make check` is allowed to mean.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = PROJECT_ROOT / "Makefile"

# Targets the T0 brief requires, plus the ones the docs tell people to run.
REQUIRED_TARGETS = [
    "setup",
    "check",
    "lint",
    "format-check",
    "types",
    "test",
    "test-slow",
    "up",
    "down",
    "health",
    "benchmark-small",
    "hooks",
    "docs-build",
]

# `make check` is the gate. It must cover all four steps, in this order.
CHECK_PREREQUISITES = ["lint", "format-check", "types", "test"]


def _targets(text: str) -> dict[str, str]:
    """Map target name to its declared prerequisites."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^([a-zA-Z][a-zA-Z0-9_-]*):(?!=)([^#]*)", line)
        if match:
            found[match.group(1)] = match.group(2).strip()
    return found


@pytest.fixture(scope="module")
def targets() -> dict[str, str]:
    return _targets(MAKEFILE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", REQUIRED_TARGETS)
def test_target_is_declared(targets: dict[str, str], name: str) -> None:
    assert name in targets, f"Makefile is missing the {name!r} target"


def test_check_runs_the_whole_gate(targets: dict[str, str]) -> None:
    prerequisites = targets["check"].split()
    assert prerequisites == CHECK_PREREQUISITES, (
        f"`make check` must be exactly {CHECK_PREREQUISITES}, found {prerequisites}"
    )


def test_fast_tests_exclude_slow_markers() -> None:
    """`make check` has to stay fast enough that people actually run it."""
    text = MAKEFILE.read_text(encoding="utf-8")
    assert 'pytest -m "not slow"' in text


def test_recipes_use_the_uv_and_compose_variables() -> None:
    """Hard-coded `uv` or `docker compose` calls break the override variables."""
    text = MAKEFILE.read_text(encoding="utf-8")
    offenders = [
        line
        for line in text.splitlines()
        if line.startswith("\t") and (line.lstrip("\t@").startswith(("uv ", "docker compose ")))
    ]
    assert not offenders, f"use $(UV) and $(COMPOSE) in recipes: {offenders}"
