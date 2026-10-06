"""Evidence: the numbers a report may cite, with where each came from.

Every governed tool result an agent collects becomes an :class:`Evidence` with a stable id
(``E1``, ``E2``, ...). The report must cite these ids, and the verifier checks each number in
the draft against the numbers of the evidence it cites. Nothing the model writes can add to the
book: only a tool result can.
"""

from __future__ import annotations

import math
from datetime import datetime  # noqa: TC003 - pydantic needs it at runtime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EvidenceKind = Literal["forecast", "calibration", "risk", "news", "fundamentals", "backtest"]
UNCALIBRATED_STATUSES = frozenset({"uncalibrated", "stale"})


def flatten_numbers(data: Any, prefix: str = "") -> dict[str, float]:
    """Every finite numeric leaf of a JSON-like value, keyed by dotted path.

    Booleans are not numbers. Strings are never parsed: a number written inside a string (for
    example inside news text) is not evidence.
    """
    found: dict[str, float] = {}
    if isinstance(data, bool):
        return found
    if isinstance(data, int | float):
        if math.isfinite(float(data)):
            found[prefix or "value"] = float(data)
        return found
    if isinstance(data, dict):
        for key, value in data.items():
            found.update(flatten_numbers(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(data, list | tuple):
        for index, value in enumerate(data):
            found.update(flatten_numbers(value, f"{prefix}[{index}]"))
    return found


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: EvidenceKind
    tool: str
    agent: str
    symbol: str | None = None
    as_of: datetime
    #: The latest moment any data behind this evidence was published. Must not be after the
    #: review's ``as_of``; the verifier checks it for every citation.
    published_at: datetime
    calibration_status: str | None = None
    numbers: dict[str, float] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    audit_seq: int | None = None
    flags: list[str] = Field(default_factory=list)

    @property
    def is_uncalibrated(self) -> bool:
        return self.calibration_status in UNCALIBRATED_STATUSES

    def line(self, max_numbers: int = 80) -> str:
        """One compact line for a prompt: id, what it is, and its numbers (no free text)."""
        status = f" ({self.calibration_status})" if self.calibration_status else ""
        subject = f" {self.symbol}" if self.symbol else ""
        shown = []
        for key, value in list(self.numbers.items())[:max_numbers]:
            shown.append(f"{key}={value:.6g}")
        flag = f" flags={','.join(self.flags)}" if self.flags else ""
        return f"[{self.id}] {self.kind}{subject}{status}: " + "; ".join(shown) + flag


class EvidenceGap(BaseModel):
    """A step whose evidence could not be collected, and why (a refusal is information)."""

    model_config = ConfigDict(extra="forbid")

    step: str
    agent: str
    tool: str
    reason: str


class EvidenceBook:
    """The ordered set of evidence collected for one review."""

    def __init__(self) -> None:
        self._items: list[Evidence] = []
        self.gaps: list[EvidenceGap] = []

    def add(self, **fields: Any) -> Evidence:
        evidence = Evidence(id=f"E{len(self._items) + 1}", **fields)
        self._items.append(evidence)
        return evidence

    def get(self, evidence_id: str) -> Evidence | None:
        for item in self._items:
            if item.id == evidence_id:
                return item
        return None

    @property
    def items(self) -> list[Evidence]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def digest(self) -> str:
        """The evidence as the composer sees it: ids and numbers, never document text."""
        return "\n".join(item.line() for item in self._items)
