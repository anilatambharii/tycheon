"""The released Kronos checkpoints, pinned to exact Hugging Face commits.

Pinning to a commit SHA rather than ``main`` means the weights a forecast was made
with cannot change underneath it, and that a compromised or re-pushed upstream repo
cannot change what Tycheon loads. All checkpoints ship as ``safetensors`` (no pickle),
so loading them does not execute code.

Facts here come from the upstream README and the Hugging Face model cards, read on
2026-10-04 (see ``docs/models/``). ``Kronos-large`` (499.2M parameters) is listed
upstream as not publicly available and is deliberately absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Variant = Literal["mini", "small", "base"]


@dataclass(frozen=True)
class KronosSpec:
    """One Kronos checkpoint and the tokenizer it must be paired with."""

    variant: Variant
    model_id: str
    model_revision: str
    tokenizer_id: str
    tokenizer_revision: str
    #: Longest sequence the model was trained for; Tycheon never feeds more.
    max_context: int
    params_millions: float

    @property
    def forecaster_id(self) -> str:
        return f"kronos-{self.variant}"

    @property
    def model_card(self) -> str:
        return f"docs/models/kronos-{self.variant}.md"

    @property
    def version(self) -> str:
        """Identifies exactly which weights and tokenizer produced a forecast."""
        model = f"{self.model_id}@{self.model_revision[:12]}"
        tokenizer = f"{self.tokenizer_id}@{self.tokenizer_revision[:12]}"
        return f"{model}+{tokenizer}"


_BASE_TOKENIZER = (
    "NeoQuasar/Kronos-Tokenizer-base",
    "0e0117387f39004a9016484a186a908917e22426",  # pragma: allowlist secret
)  # pragma: allowlist secret

KRONOS_SPECS: dict[Variant, KronosSpec] = {
    "mini": KronosSpec(
        variant="mini",
        model_id="NeoQuasar/Kronos-mini",
        model_revision="f4e68697d9d5aed55cef5c96aabc3376bcad9f81",  # pragma: allowlist secret
        tokenizer_id="NeoQuasar/Kronos-Tokenizer-2k",
        tokenizer_revision="26966d0035065a0cae0ebad7af8ece35bc1fb51c",  # pragma: allowlist secret
        max_context=2048,
        params_millions=4.1,
    ),
    "small": KronosSpec(
        variant="small",
        model_id="NeoQuasar/Kronos-small",
        model_revision="901c26c1332695a2a8f243eb2f37243a37bea320",  # pragma: allowlist secret
        tokenizer_id=_BASE_TOKENIZER[0],
        tokenizer_revision=_BASE_TOKENIZER[1],
        max_context=512,
        params_millions=24.7,
    ),
    "base": KronosSpec(
        variant="base",
        model_id="NeoQuasar/Kronos-base",
        model_revision="2b554741eca47781b64468546e77fef3e85130e6",  # pragma: allowlist secret
        tokenizer_id=_BASE_TOKENIZER[0],
        tokenizer_revision=_BASE_TOKENIZER[1],
        max_context=512,
        params_millions=102.3,
    ),
}
