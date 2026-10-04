"""A fresh clone must contain everything setup, check and the dev stack need."""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "Makefile",
    "README.md",
    "SECURITY.md",
    ".env.example",
    ".gitattributes",
    ".gitleaks.toml",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".secrets.baseline",
    "docker-compose.dev.yml",
    "mkdocs.yml",
    "pyproject.toml",
    "ee/LICENSE",
    "benchmarks/README.md",
    "benchmarks/configs/small.yaml",
    "benchmarks/run.py",
    "deploy/README.md",
    "docs/index.md",
    "docs/methodology.md",
    "docs/safety.md",
    "docs/adr/0001-licensing-and-open-core.md",
    "docs/adr/0002-kronos-integration.md",
    "docs/adr/0003-point-in-time-data-and-forecast-contract.md",
    "docs/models/index.md",
    "examples/README.md",
    "examples/forecast.py",
    "third_party/kronos/LICENSE",
    "third_party/kronos/README.md",
    "third_party/kronos/UPSTREAM.json",
    "third_party/kronos/model/kronos.py",
    "third_party/kronos/model/module.py",
    "src/tycheon/py.typed",
    "src/tycheon/agents/README.md",
    "src/tycheon/governance/README.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/workflows/ci.yml",
    ".github/workflows/codeql.yml",
    ".github/workflows/dependency-review.yml",
    ".github/workflows/nightly-slow.yml",
    ".github/workflows/secret-scan.yml",
]


# One card per forecaster: AGENTS.md says no forecast without a model card reference.
MODEL_CARDS = [
    "kronos-mini",
    "kronos-small",
    "kronos-base",
    "timesfm",
    "chronos-2",
    "random-walk",
    "drift",
    "seasonal-naive",
    "arima",
    "garch",
]
REQUIRED_FILES += [f"docs/models/{name}.md" for name in MODEL_CARDS]


@pytest.mark.parametrize("relpath", REQUIRED_FILES)
def test_required_file_exists(relpath: str) -> None:
    assert (PROJECT_ROOT / relpath).is_file(), f"missing {relpath}"


def test_env_example_contains_no_filled_secret() -> None:
    """The example env is committed, so every secret-ish key must be empty."""
    secretish = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "SIGNING_KEY")
    offenders: list[str] = []
    for raw in (PROJECT_ROOT / ".env.example").read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if any(marker in key.upper() for marker in secretish) and value.strip():
            offenders.append(key)
    assert not offenders, f"committed example has filled secrets: {offenders}"


def test_env_example_documents_the_live_execution_switch() -> None:
    text = (PROJECT_ROOT / ".env.example").read_text()
    assert "TYCHEON_ALLOW_LIVE_EXECUTION=false" in text


def test_env_example_defaults_yfinance_off() -> None:
    """yfinance is local-dev only; the committed default must not enable it."""
    text = (PROJECT_ROOT / ".env.example").read_text()
    assert "TYCHEON_ALLOW_YFINANCE=false" in text


def test_ee_is_not_apache_licensed() -> None:
    """The open-core boundary: ee/ must carry its own proprietary license."""
    ee_license = (PROJECT_ROOT / "ee" / "LICENSE").read_text()
    assert "Apache License, Version 2.0 that governs" in ee_license
    assert "PROPRIETARY" in ee_license


def test_not_investment_advice_disclaimer_is_published() -> None:
    """AGENTS.md requires the disclaimer on user-facing surfaces."""
    for relpath in ("README.md", "docs/index.md"):
        text = (PROJECT_ROOT / relpath).read_text().lower()
        assert "not investment advice" in text, f"{relpath} is missing the disclaimer"


def test_no_unfinished_markers_ship_in_the_docs() -> None:
    """AGENTS.md: no TODO without an issue. Docs must not carry draft markers either."""
    markers = ("PLACEHOLDER", "TODO", "TBD", "FIXME", "XXX", "<<")
    paths = [
        *PROJECT_ROOT.glob("docs/**/*.md"),
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "benchmarks" / "README.md",
        PROJECT_ROOT / "examples" / "README.md",
    ]
    offenders = [
        f"{path.relative_to(PROJECT_ROOT)}: {marker}"
        for path in paths
        for marker in markers
        if marker in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"unfinished markers: {offenders}"
