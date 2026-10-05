"""Model registry for the benchmark: per-model YAML configs to forecaster factories.

Each model has a file in ``benchmarks/configs/models/``. Baselines and foundation models are
plain forecasters; the two Tycheon models (``tycheon-ensemble``, ``tycheon-calibrated``) are
built from training history at every refit by the factories in :mod:`tycheon.backtest`.

A model whose optional extra is not installed is reported as *skipped, with the reason*; it is
never silently dropped from the leaderboard.
"""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from tycheon.backtest import (
    WalkForwardConfig,
    calibrated_ensemble_factory,
    ensemble_factory,
    static,
)
from tycheon.calibration import AdaptiveConformal, SplitConformal

if TYPE_CHECKING:
    from collections.abc import Callable

    from tycheon.backtest import ForecasterFactory
    from tycheon.models.base import Forecaster

CONFIG_DIR = Path(__file__).resolve().parent / "configs" / "models"

_BASELINE_MODULE = "tycheon.models.baselines"
_FOUNDATION_MODULES = {
    "KronosForecaster": "tycheon.models.kronos",
    "TimesFMForecaster": "tycheon.models.timesfm",
    "ChronosForecaster": "tycheon.models.chronos",
}
#: Python module whose presence tells us an optional extra is installed.
_EXTRA_PROBES = {"kronos": "torch", "timesfm": "timesfm", "chronos": "chronos"}

TYCHEON_MODELS = ("tycheon-ensemble", "tycheon-calibrated")


class ModelConfigError(ValueError):
    """A model config that is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class ModelSpec:
    id: str
    kind: str  # baseline | foundation | tycheon
    config: dict[str, Any]

    @property
    def options(self) -> dict[str, Any]:
        return dict(self.config.get("options", {}))


def load_model_spec(model_id: str, config_dir: Path = CONFIG_DIR) -> ModelSpec:
    path = config_dir / f"{model_id}.yaml"
    if not path.is_file():
        raise ModelConfigError(f"no config for model {model_id!r} (expected {path})")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("id") != model_id or "kind" not in raw:
        raise ModelConfigError(f"{path}: needs 'id: {model_id}' and a 'kind'")
    if raw["kind"] not in ("baseline", "foundation", "tycheon"):
        raise ModelConfigError(f"{path}: unknown kind {raw['kind']!r}")
    return ModelSpec(model_id, raw["kind"], dict(raw))


def unavailable_reason(spec: ModelSpec, specs: dict[str, ModelSpec] | None = None) -> str | None:
    """Why ``spec`` cannot run here, or ``None`` if it can."""
    extra = spec.config.get("extra")
    if extra:
        probe = _EXTRA_PROBES.get(extra, extra)
        if importlib.util.find_spec(probe) is None:
            return f"optional extra not installed: pip install 'tycheon[{extra}]'"
    if spec.kind == "tycheon":
        for member in spec.config.get("members", []):
            member_spec = (specs or {}).get(member) or load_model_spec(member)
            reason = unavailable_reason(member_spec)
            if reason:
                return f"ensemble member {member} unavailable ({reason})"
    return None


def _constructor(spec: ModelSpec) -> Callable[[], Forecaster]:
    name = spec.config["class"]
    module = _FOUNDATION_MODULES.get(name, _BASELINE_MODULE)
    cls = getattr(importlib.import_module(module), name)
    options = spec.options
    if "order" in options:
        options["order"] = tuple(options["order"])
    return lambda: cls(**options)  # type: ignore[no-any-return]


def effective_walk_forward(spec: ModelSpec, base: WalkForwardConfig) -> WalkForwardConfig:
    """The shared walk-forward layout with this model's declared overrides applied.

    Only ``n_samples`` and ``max_history`` can be overridden; the layout (folds, windows,
    embargo, refit) is identical for every model so they are scored at the same origins.
    """
    override = dict(spec.config.get("walk_forward", {}))
    unknown = set(override) - {"n_samples", "max_history"}
    if unknown:
        raise ModelConfigError(
            f"{spec.id}: walk_forward may only override n_samples and max_history"
        )
    return replace(base, **override)


def make_factory(
    spec: ModelSpec, horizon: int, specs: dict[str, ModelSpec] | None = None
) -> ForecasterFactory:
    """A walk-forward factory for ``spec``."""
    if spec.kind != "tycheon":
        return static(_constructor(spec)())
    members = {}
    for member in spec.config["members"]:
        member_spec = (specs or {}).get(member) or load_model_spec(member)
        members[member] = _constructor(member_spec)
    common: dict[str, Any] = {
        "horizon": horizon,
        "n_origins": spec.config.get("n_origins", 60),
        "n_samples": spec.config.get("n_samples", 60),
        "max_history": spec.config.get("max_history", 500),
    }
    if spec.id == "tycheon-ensemble":
        return ensemble_factory(members, **common)
    if spec.id == "tycheon-calibrated":
        kind = spec.config.get("calibration", {}).get("method", "adaptive")
        method = AdaptiveConformal if kind == "adaptive" else SplitConformal
        return calibrated_ensemble_factory(members, method=method, model_id=spec.id, **common)
    raise ModelConfigError(f"unknown tycheon model {spec.id!r}")
