"""Plans as configuration, loaded from ``plans.toml`` and validated.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from typing import Any


class PlanError(ValueError):
    """The plans file is malformed, or a plan name is unknown."""


@dataclass(frozen=True)
class Price:
    amount_cents: int
    currency: str
    interval: str
    lookup_key: str


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    description: str
    research_use_only: bool
    data_frequencies: tuple[str, ...]
    features: frozenset[str]
    rate_limit_per_minute: int
    retention_days: int
    trial_days: int
    limits: dict[str, int] = field(default_factory=dict)
    price: Price | None = None

    def has(self, feature: str) -> bool:
        return feature in self.features

    def limit(self, kind: str, override: dict[str, Any] | None = None) -> int:
        """The monthly limit for ``kind``; an organisation override wins; unknown means zero."""
        if override and kind in override:
            value = override[kind]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise PlanError(f"limit override for {kind!r} must be a non-negative integer")
            return value
        return self.limits.get(kind, 0)


@dataclass(frozen=True)
class Plans:
    plans: dict[str, Plan]
    meters: dict[str, str]

    def get(self, key: str) -> Plan:
        try:
            return self.plans[key]
        except KeyError:
            raise PlanError(f"unknown plan {key!r}") from None

    @property
    def default(self) -> Plan:
        return self.get("developer")


def parse_plans(document: dict[str, Any]) -> Plans:
    meters = dict(document.get("meters", {}))
    if not meters:
        raise PlanError("plans file has no [meters]")
    plans: dict[str, Plan] = {}
    for key, raw in document.get("plans", {}).items():
        limits = {k: int(v) for k, v in raw.get("limits", {}).items()}
        unknown = set(limits) - set(meters)
        if unknown:
            raise PlanError(f"plan {key!r} limits an unknown meter: {sorted(unknown)}")
        if any(v < 0 for v in limits.values()):
            raise PlanError(f"plan {key!r} has a negative limit")
        price = raw.get("price")
        plans[key] = Plan(
            key=key,
            name=str(raw["name"]),
            description=str(raw.get("description", "")),
            research_use_only=bool(raw.get("research_use_only", False)),
            data_frequencies=tuple(raw.get("data_frequencies", ["1D"])),
            features=frozenset(raw.get("features", [])),
            rate_limit_per_minute=int(raw["rate_limit_per_minute"]),
            retention_days=int(raw["retention_days"]),
            trial_days=int(raw.get("trial_days", 0)),
            limits=limits,
            price=Price(**price) if price else None,
        )
    if "developer" not in plans:
        raise PlanError("plans file must define the free 'developer' plan")
    return Plans(plans=plans, meters=meters)


def load_plans() -> Plans:
    text = resources.files("tycheon_cp").joinpath("plans.toml").read_text(encoding="utf-8")
    return parse_plans(tomllib.loads(text))
