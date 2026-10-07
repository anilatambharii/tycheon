"""Base models for fine-tuning, and turning trained weights back into a Tycheon forecaster.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tycheon.models.kronos import KronosForecaster
from tycheon.models.kronos.specs import KRONOS_SPECS
from tycheon.models.kronos.vendor import load_upstream

if TYPE_CHECKING:
    from tycheon.models.base import Forecaster


class PinnedKronos:
    """The released Kronos checkpoint, at the exact commit Tycheon pins (safetensors, no pickle)."""

    def __init__(self, variant: str = "mini") -> None:
        if variant not in KRONOS_SPECS:
            raise ValueError(f"unknown Kronos variant {variant!r}")
        self.variant: Any = variant
        self.spec = KRONOS_SPECS[variant]
        self.max_context = self.spec.max_context

    def load(self) -> tuple[Any, Any]:
        upstream = load_upstream()
        tokenizer = upstream.tokenizer_cls.from_pretrained(
            self.spec.tokenizer_id, revision=self.spec.tokenizer_revision
        )
        model = upstream.kronos_cls.from_pretrained(
            self.spec.model_id, revision=self.spec.model_revision
        )
        return tokenizer, model


def forecaster_from(
    tokenizer: Any,
    model: Any,
    *,
    variant: str,
    max_context: int,
    name: str,
    card: str,
    lookback: int | None = None,
    device: str = "cpu",
) -> Forecaster:
    """A Tycheon forecaster around injected weights, labelled so its provenance is honest."""
    forecaster = KronosForecaster(
        variant=variant,  # type: ignore[arg-type]
        device=device,
        lookback=lookback,
        tokenizer=tokenizer,
        model=model,
        max_context=max_context,
    )
    forecaster.model_id = name
    forecaster.model_card = card
    return forecaster
