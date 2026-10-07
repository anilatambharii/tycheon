"""The seams between the agents and everything else.

The agents are pure Tycheon code: they import no Keelgate. They are handed three things:

* a :class:`ToolCaller` that runs a named tool *as a named agent* (the governance layer
  implements it over Keelgate's gateway: grant, policy, approval, audit);
* a :class:`TextModel` for the two steps that use a language model (planning and drafting);
* a :class:`Tracer` for spans.

Nothing here can execute an action by itself, which is the point: whatever an agent does, it
does by asking a ``ToolCaller``, and the caller's answer is a :class:`ToolResult` the agent
must read, including when the answer is "no".
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

ToolStatus = Literal["OK", "DENIED", "APPROVAL_REQUIRED", "ERROR"]


@dataclass(frozen=True)
class EvidenceRef:
    """A pointer a human approver can follow: where a claim came from."""

    uri: str
    title: str | None = None


@dataclass(frozen=True)
class ToolResult:
    """What a governed tool call returned. A refusal is a result, not an exception."""

    status: ToolStatus
    tool: str
    call_id: str = ""
    output: dict[str, Any] | None = None
    error_code: str | None = None
    message: str = ""
    approval_id: str | None = None
    approval_tier: str | None = None
    policy_effect: str | None = None
    policy_reasons: tuple[str, ...] = ()
    audit_seq: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "OK"


class ToolCaller(Protocol):
    async def call(
        self,
        agent: str,
        tool: str,
        arguments: Mapping[str, Any],
        *,
        rationale: str = "",
        evidence: Sequence[EvidenceRef] = (),
        confidence: float | None = None,
        flags: Sequence[str] = (),
        approval_id: str | None = None,
    ) -> ToolResult: ...


@dataclass(frozen=True)
class TextResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class TextModel(Protocol):
    """A language model as the agents need it: a prompt in, text out, with token counts."""

    async def complete(self, *, system: str, prompt: str, purpose: str = "") -> TextResult: ...


class Span(Protocol):
    def set_attribute(self, key: str, value: Any) -> None: ...


class Tracer(Protocol):
    def span(self, name: str, **attributes: Any) -> contextlib.AbstractContextManager[Span]: ...


class _NullSpan:
    def set_attribute(self, key: str, value: Any) -> None:  # noqa: ARG002
        return None


class NullTracer:
    """Records nothing. The default, so the agents never require an exporter."""

    @contextlib.contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:  # noqa: ARG002
        yield _NullSpan()


@dataclass
class TraceEvent:
    """One line of the human-readable run trace (what the example prints)."""

    actor: str
    action: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)
