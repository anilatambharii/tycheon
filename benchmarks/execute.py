"""Run a benchmark config end to end and produce a versioned result document.

Every model is scored by the same walk-forward engine at the same origins, then read against
the random walk with Diebold-Mariano tests. Models that cannot run here are recorded as
skipped or failed with the reason; they are never dropped. A :class:`LookaheadError` is never
caught: a leaky run must stop, not be reported.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np

import tycheon
from benchmarks.datasets import get_dataset, load_series, manifest_sha256
from benchmarks.models import (
    ModelConfigError,
    effective_walk_forward,
    load_model_spec,
    make_factory,
    unavailable_reason,
)
from tycheon.backtest import (
    CostModel,
    WalkForwardConfig,
    evaluate,
    strategy_result,
    survivorship_warning,
    walk_forward,
)
from tycheon.backtest.metrics import pool_scores
from tycheon.errors import LookaheadError, ModelError, OptionalDependencyError
from tycheon.models.base import DISCLAIMER

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pandas as pd

    from tycheon.calibration.scores import ScoreSet

SCHEMA_VERSION = 1
RANDOM_WALK = "random-walk"
WF_KEYS = ("folds", "train_window", "test_window", "embargo", "refit", "n_samples",
           "max_history", "min_history", "stride", "seed")  # fmt: skip

#: Errors that mean "this model could not run here" (no weights, no network, missing extra).
#: LookaheadError is deliberately absent: it must stop the run.
RECOVERABLE = (OptionalDependencyError, ModelError, OSError, ImportError)


def clean(obj: Any) -> Any:
    """JSON-safe: NaN and infinity become ``null``; numpy scalars become Python."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, np.generic):
        return clean(obj.item())
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def config_sha256(config: dict[str, Any]) -> str:
    blob = json.dumps(clean(config), sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            capture_output=True, text=True, timeout=10, check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def environment() -> dict[str, Any]:
    """What the run used. No hostnames, usernames or paths."""
    info: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
        "numpy": np.__version__,
        "device": "cpu",
    }
    try:
        import torch

        info["torch"] = torch.__version__
        info["device"] = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        info["torch"] = None
    return info


def wf_config(config: dict[str, Any], horizon: int) -> WalkForwardConfig:
    raw = {k: v for k, v in config["walk_forward"].items() if k in WF_KEYS}
    return WalkForwardConfig(horizon=horizon, **raw)


def _name(scores: ScoreSet, model_id: str) -> ScoreSet:
    return replace(scores, model_id=model_id)


def run_model(
    model_id: str,
    *,
    symbols: list[str],
    bars: dict[str, pd.DataFrame],
    base: WalkForwardConfig,
    specs: dict[str, Any],
    say: Callable[[str], None],
) -> dict[str, Any]:
    """Walk-forward one model over every symbol. Returns scores and bookkeeping, or why not."""
    spec = specs[model_id]
    reason = unavailable_reason(spec, specs)
    if reason:
        return {"status": "skipped", "reason": reason}
    wf = effective_walk_forward(spec, base)
    started = time.perf_counter()
    scores: dict[str, ScoreSet] = {}
    checks, refits, seconds = 0, 0, 0.0
    try:
        for symbol in symbols:
            result = walk_forward(make_factory(spec, base.horizon, specs), bars[symbol], wf)
            scores[symbol] = _name(result.scores, model_id)
            checks += result.guard_checks
            refits += len(result.refits)
            seconds += result.seconds
            say(f"    {model_id:<20} {symbol:<11} {result.scores.n} origins, {result.seconds:.1f}s")
    except LookaheadError:
        raise
    except RECOVERABLE as exc:
        return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:300]}
    return {
        "status": "ran",
        "scores": scores,
        "guard_checks": checks,
        "refits": refits,
        "seconds": time.perf_counter() - started,
        "n_samples": wf.n_samples,
        "max_history": wf.max_history,
    }


def evaluate_horizon(
    ran: dict[str, dict[str, Any]], symbols: list[str], cost: CostModel, horizon: int, stride: int
) -> dict[str, Any]:
    """Pooled and per-series evaluation of every model that ran, against the random walk."""
    rw = ran[RANDOM_WALK]["scores"]
    out: dict[str, Any] = {}
    for model_id, record in ran.items():
        scores = record["scores"]
        pooled = evaluate(pool_scores(list(scores.values())), pool_scores(list(rw.values())))
        entry: dict[str, Any] = {
            "pooled": pooled.to_dict(),
            "by_series": {s: evaluate(scores[s], rw[s]).to_dict() for s in symbols},
            "leakage_controls": {
                "guard_checks": record["guard_checks"],
                "refits": record["refits"],
            },
            "n_samples": record["n_samples"],
            "max_history": record["max_history"],
            "seconds": round(record["seconds"], 2),
        }
        if stride >= horizon:
            per = [strategy_result(scores[s], cost) for s in symbols]
            entry["strategy"] = {
                "gross_mean": float(np.mean([p.gross_mean for p in per])),
                "net_mean": float(np.mean([p.net_mean for p in per])),
                "turnover_per_period": float(np.mean([p.turnover / p.n_periods for p in per])),
                "hit_rate": float(np.nanmean([p.hit_rate for p in per])),
                "note": "naive sign strategy; research diagnostic, not a recommendation",
            }
        out[model_id] = entry
    return out


def execute(
    config: dict[str, Any],
    *,
    data_dir: Path | None = None,
    only: set[str] | None = None,
    say: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run ``config`` and return the result document (see ``docs/benchmark-methodology.md``)."""
    started = time.perf_counter()
    dataset = get_dataset(config["dataset"])
    symbols: list[str] = list(config["universe"])
    bars = {s: load_series(dataset, s, data_dir) for s in symbols}
    model_ids = [
        RANDOM_WALK,
        *[b for b in config["baselines"] if b != RANDOM_WALK],
        *config["models"],
    ]
    if only:
        model_ids = [m for m in model_ids if m == RANDOM_WALK or m in only]
    specs = {m: load_model_spec(m) for m in model_ids}
    for spec in list(specs.values()):  # member specs for tycheon models
        for member in spec.config.get("members", []):
            specs.setdefault(member, load_model_spec(member))
    cost = CostModel(**config.get("costs", {}))

    warnings: list[str] = []
    warning = survivorship_warning(
        symbols, universe_kind=config.get("universe_kind", "static"), synthetic=dataset.synthetic
    )
    if warning:
        warnings.append(warning)
    if not dataset.redistributable:
        warnings.append(
            "this dataset is not redistributable: publish these results only if your data "
            "licence allows it"
        )

    horizons: list[dict[str, Any]] = []
    status: dict[str, dict[str, Any]] = {}
    for horizon in config["horizons"]:
        say(f"horizon {horizon}")
        base = wf_config(config, horizon)
        ran: dict[str, dict[str, Any]] = {}
        for model_id in model_ids:
            record = run_model(
                model_id, symbols=symbols, bars=bars, base=base, specs=specs, say=say
            )
            if record["status"] == "ran":
                ran[model_id] = record
            elif model_id == RANDOM_WALK:
                raise ModelConfigError(f"the random walk could not run: {record['reason']}")
            status[model_id] = {k: v for k, v in record.items() if k in ("status", "reason")}
            if record["status"] != "ran":
                say(f"    {model_id:<20} {record['status']}: {record['reason']}")
        horizons.append(
            {
                "horizon": horizon,
                "models": evaluate_horizon(ran, symbols, cost, horizon, base.step),
                "n_origins_per_series": next(iter(ran[RANDOM_WALK]["scores"].values())).n,
            }
        )
        for model_id in ran:
            status[model_id] = {"status": "ran"}

    return clean(
        {
            "schema_version": SCHEMA_VERSION,
            "benchmark": config["name"],
            "tycheon_version": tycheon.__version__,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "git": {"sha": _git("rev-parse", "HEAD"), "dirty": bool(_git("status", "--porcelain"))},
            "config": config,
            "config_sha256": config_sha256(config),
            "dataset": {
                "name": dataset.name,
                "synthetic": dataset.synthetic,
                "redistributable": dataset.redistributable,
                "manifest_sha256": manifest_sha256(),
                "series": [
                    {
                        "symbol": s,
                        "n_bars": len(bars[s]),
                        "first": bars[s].index[0].isoformat(),
                        "last": bars[s].index[-1].isoformat(),
                    }
                    for s in symbols
                ],
            },
            "environment": environment(),
            "warnings": warnings,
            "model_status": status,
            "results": horizons,
            "runtime_seconds": round(time.perf_counter() - started, 1),
            "disclaimer": DISCLAIMER,
        }
    )


def main_stdout(message: str) -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()
