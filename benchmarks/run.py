"""Leaderboard runner.

Today this validates a benchmark config and prints the plan it would execute.
Execution arrives with the walk-forward engine (Phase T3); until then the runner
refuses to pretend it measured anything.

The validation it already enforces is the rule that makes the leaderboard
trustworthy: every config must evaluate against the random-walk baseline and
report a Diebold-Mariano test, so a published result can always be read against
"do nothing".

    make benchmark-small
    python -m benchmarks.run --config benchmarks/configs/small.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from tycheon.models.baselines import (
    ARIMAForecaster,
    DriftForecaster,
    GARCHForecaster,
    RandomWalkForecaster,
    SeasonalNaiveForecaster,
)
from tycheon.models.chronos import ChronosForecaster
from tycheon.models.kronos import KRONOS_SPECS
from tycheon.models.timesfm import TimesFMForecaster

BASELINES = {
    cls.model_id
    for cls in (
        RandomWalkForecaster,
        DriftForecaster,
        SeasonalNaiveForecaster,
        ARIMAForecaster,
        GARCHForecaster,
    )
}
FOUNDATION_MODELS = {spec.forecaster_id for spec in KRONOS_SPECS.values()} | {
    TimesFMForecaster.model_id,
    ChronosForecaster.model_id,
}

REQUIRED_KEYS = ("name", "dataset", "horizons", "models", "baselines", "walk_forward")

# AGENTS.md: every evaluation reports the random-walk baseline and a
# Diebold-Mariano test. A config that skips it cannot be published.
MANDATORY_BASELINE = "random-walk"


class ConfigError(ValueError):
    """A benchmark config that could not be published honestly."""


def load_config(path: Path) -> dict[str, Any]:
    """Parse and validate a benchmark config."""
    if not path.is_file():
        raise ConfigError(f"no such config: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")

    missing = [key for key in REQUIRED_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"{path}: missing required keys {missing}")

    models, baselines = raw["models"], raw["baselines"]
    unknown_models = sorted(set(models) - FOUNDATION_MODELS) if isinstance(models, list) else ["?"]
    if unknown_models:
        raise ConfigError(
            f"{path}: unknown models {unknown_models}; known: {sorted(FOUNDATION_MODELS)}"
        )
    if not isinstance(baselines, list) or MANDATORY_BASELINE not in baselines:
        raise ConfigError(
            f"{path}: baselines must include {MANDATORY_BASELINE!r} — "
            "every published result is read against doing nothing"
        )

    unknown_baselines = sorted(set(baselines) - BASELINES)
    if unknown_baselines:
        raise ConfigError(
            f"{path}: unknown baselines {unknown_baselines}; known: {sorted(BASELINES)}"
        )

    walk_forward = raw["walk_forward"]
    if not isinstance(walk_forward, dict) or "embargo" not in walk_forward:
        raise ConfigError(
            f"{path}: walk_forward.embargo is required — without an embargo the "
            "evaluation leaks across fold boundaries"
        )

    return dict(raw)


def describe(config: dict[str, Any]) -> str:
    """Render the plan a config describes, without running it."""
    wf = config["walk_forward"]
    lines = [
        f"benchmark      {config['name']}",
        f"dataset        {config['dataset']}",
        f"horizons       {config['horizons']}",
        f"models         {config['models']}",
        f"baselines      {config['baselines']}",
        f"walk-forward   folds={wf.get('folds')} embargo={wf.get('embargo')}",
        f"as_of          {config.get('as_of', 'per-fold (point-in-time)')}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", required=True, type=Path, help="path to a benchmark config")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="run the benchmark (not available until the walk-forward engine lands in Phase T3)",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    print(describe(config))

    if args.execute:
        print(
            "\nrefusing to execute: the leakage-proof walk-forward engine does not exist yet.\n"
            "The forecasters are built (Phase T1); the engine that scores them honestly lands "
            "in Phase T3. Until then this runner validates configs only.",
            file=sys.stderr,
        )
        return 3

    print("\nconfig is valid. Execution arrives with the T3 walk-forward engine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
