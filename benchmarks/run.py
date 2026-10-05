"""Leaderboard runner: validate a benchmark config, run it, publish versioned JSON results.

    make benchmark-small                                   # CPU, sample data, minutes
    python -m benchmarks.run --config benchmarks/configs/small.yaml          # validate + plan
    python -m benchmarks.run --config benchmarks/configs/small.yaml --execute
    python -m benchmarks.run --config benchmarks/configs/full.yaml --execute --data-dir ./my_csvs

Validation enforces the rules that make the leaderboard trustworthy: every config must evaluate
against the random-walk baseline (read with a Diebold-Mariano test), must set an embargo, and
must use non-overlapping origins so pooled tests are valid. A model whose optional extra is not
installed is reported as skipped with the reason, never silently dropped.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from benchmarks.datasets import DatasetError, get_dataset
from benchmarks.models import TYCHEON_MODELS
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
KNOWN_MODELS = FOUNDATION_MODELS | set(TYCHEON_MODELS)

REQUIRED_KEYS = ("name", "dataset", "horizons", "models", "baselines", "walk_forward")

# AGENTS.md: every evaluation reports the random-walk baseline and a
# Diebold-Mariano test. A config that skips it cannot be published.
MANDATORY_BASELINE = "random-walk"

RESULTS_DIR = Path(__file__).resolve().parent / "results"


class ConfigError(ValueError):
    """A benchmark config that could not be published honestly."""


def _check_models(path: Path, raw: dict[str, Any]) -> None:
    models, baselines = raw["models"], raw["baselines"]
    unknown_models = sorted(set(models) - KNOWN_MODELS) if isinstance(models, list) else ["?"]
    if unknown_models:
        raise ConfigError(f"{path}: unknown models {unknown_models}; known: {sorted(KNOWN_MODELS)}")
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


def _check_walk_forward(path: Path, raw: dict[str, Any]) -> None:
    walk_forward = raw["walk_forward"]
    if not isinstance(walk_forward, dict) or "embargo" not in walk_forward:
        raise ConfigError(
            f"{path}: walk_forward.embargo is required — without an embargo the "
            "evaluation leaks across fold boundaries"
        )
    horizons = raw["horizons"]
    if (
        not isinstance(horizons, list)
        or not horizons
        or not all(isinstance(h, int) and h >= 1 for h in horizons)
    ):
        raise ConfigError(f"{path}: horizons must be a non-empty list of positive integers")
    stride = walk_forward.get("stride")
    if stride is not None and stride < max(horizons):
        raise ConfigError(
            f"{path}: walk_forward.stride must be at least the largest horizon; overlapping "
            "origins break the pooled Diebold-Mariano test"
        )


def _check_data(path: Path, raw: dict[str, Any]) -> None:
    try:
        dataset = get_dataset(raw["dataset"])
    except DatasetError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    universe = raw.get("universe")
    if not isinstance(universe, list) or not universe:
        raise ConfigError(f"{path}: universe must be a non-empty list of symbols")
    allowed = dataset.entry.get("symbols")
    if allowed is not None:
        outside = sorted(set(universe) - set(allowed))
        if outside:
            raise ConfigError(f"{path}: {outside} are not in dataset {dataset.name!r}")
    if raw.get("universe_kind", "static") not in ("static", "point_in_time"):
        raise ConfigError(f"{path}: universe_kind must be 'static' or 'point_in_time'")


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

    _check_models(path, raw)
    _check_walk_forward(path, raw)
    _check_data(path, raw)
    return dict(raw)


def describe(config: dict[str, Any]) -> str:
    """Render the plan a config describes, without running it."""
    wf = config["walk_forward"]
    lines = [
        f"benchmark      {config['name']}",
        f"dataset        {config['dataset']}",
        f"universe       {config.get('universe')} ({config.get('universe_kind', 'static')})",
        f"horizons       {config['horizons']}",
        f"models         {config['models']}",
        f"baselines      {config['baselines']}",
        f"walk-forward   folds={wf.get('folds')} embargo={wf.get('embargo')} "
        f"refit={wf.get('refit', 'fold')}",
        f"as_of          {config.get('as_of', 'per-fold (point-in-time)')}",
    ]
    return "\n".join(lines)


def write_result(result: dict[str, Any], out_dir: Path) -> Path:
    """Write ``<out_dir>/<benchmark>/<tycheon version>.json`` and return its path."""
    target = out_dir / result["benchmark"] / f"{result['tycheon_version']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n"
    )
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", required=True, type=Path, help="path to a benchmark config")
    parser.add_argument("--execute", action="store_true", help="run the benchmark")
    parser.add_argument("--data-dir", type=Path, default=None, help="your own licensed CSV data")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="where to write results")
    parser.add_argument("--only", nargs="*", default=None, help="run only these models")
    parser.add_argument("--no-render", action="store_true", help="do not render the leaderboard")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    print(describe(config))
    if not args.execute:
        print("\nconfig is valid. Add --execute to run it.")
        return 0

    from benchmarks.execute import execute  # heavy imports only when running

    try:
        result = execute(config, data_dir=args.data_dir, only=set(args.only or []) or None)
    except DatasetError as exc:
        print(f"dataset error: {exc}", file=sys.stderr)
        return 2
    path = write_result(result, args.out)
    print(f"\nwrote {path} ({result['runtime_seconds']}s)")

    if not args.no_render:
        from benchmarks.render import render_leaderboard

        page = render_leaderboard(args.out)
        print(f"rendered {page}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
