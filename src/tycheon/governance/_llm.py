"""Language models for the agents, through Keelgate's model-agnostic ``LLMClient``.

The agents only need ``TextModel`` (a prompt in, text and token counts out). This adapter wraps a
Keelgate ``LLMClient`` (real providers, or the scripted ``FakeLLM`` used in CI) in that shape and
records a GenAI-convention span per call.

A model's reply is only ever a proposal: the planner's JSON is validated and clamped, and a draft
is verified. Nothing a model says can execute anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from keelgate.llm import LLMClient, LLMRequest, Message, Role
from keelgate.testing import FakeLLM, Reply

from tycheon.agents.protocols import NullTracer, TextResult

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from tycheon.agents.protocols import Tracer


class KeelgateTextModel:
    """A :class:`~tycheon.agents.protocols.TextModel` over a Keelgate ``LLMClient``."""

    def __init__(
        self,
        client: LLMClient,
        model: str,
        *,
        max_tokens: int = 1200,
        tracer: Tracer | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._tracer = tracer or NullTracer()

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, *, system: str, prompt: str, purpose: str = "") -> TextResult:
        request = LLMRequest(
            model=self._model,
            messages=(
                Message(role=Role.SYSTEM, content=system),
                Message(role=Role.USER, content=prompt),
            ),
            max_tokens=self._max_tokens,
            metadata={"purpose": purpose},
        )
        with self._tracer.span(
            f"chat {self._model}",
            **{
                "gen_ai.operation.name": "chat",
                "gen_ai.system": self._client.name,
                "gen_ai.request.model": self._model,
                "tycheon.purpose": purpose,
            },
        ) as span:
            response = await self._client.complete(request)
            span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens)
        return TextResult(
            text=response.text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


def _as_script_item(
    item: str | Callable[[str, str], str],
) -> str | Callable[[LLMRequest], Reply]:
    """A plain reply, or a function of ``(system, prompt)`` that writes one from the prompt."""
    if isinstance(item, str):
        return item

    def reply(request: LLMRequest) -> Reply:
        system = next((m.content for m in request.messages if m.role is Role.SYSTEM), "")
        prompt = next((m.content for m in reversed(request.messages) if m.role is Role.USER), "")
        return Reply.say(item(system, prompt))

    return reply


def scripted_text_model(
    replies: Sequence[str | Callable[[str, str], str]], *, tracer: Tracer | None = None
) -> tuple[KeelgateTextModel, FakeLLM]:
    """A deterministic model for CI and tests: ``replies`` are used in order, one per call.

    Each reply is a string, or a function ``(system, prompt) -> text`` that writes the reply from
    the prompt it is shown (how the demo drafter quotes real evidence numbers). Returns the model
    and the underlying ``FakeLLM`` so a test can inspect every request the agents sent, for
    example to prove no untrusted text ever reached a prompt.
    """
    fake = FakeLLM([_as_script_item(r) for r in replies])
    return KeelgateTextModel(fake, "scripted-model", tracer=tracer), fake


def make_text_model(
    provider: str, model: str, *, tracer: Tracer | None = None, **options: Any
) -> KeelgateTextModel:
    """A real model by provider name: ``anthropic``, ``openai`` or ``ollama``.

    Keys are read from the environment by the provider SDKs (for example ``ANTHROPIC_API_KEY``);
    nothing is stored here. Optional SDKs are imported only when asked for.
    """
    name = provider.lower()
    client: LLMClient
    if name == "anthropic":
        from keelgate.llm.providers import AnthropicClient

        client = AnthropicClient(**options)
    elif name == "openai":
        from keelgate.llm.providers import OpenAIClient

        client = OpenAIClient(**options)
    elif name == "ollama":
        from keelgate.llm.providers import OllamaClient

        client = OllamaClient(**options)
    else:
        raise ValueError(f"unknown LLM provider {provider!r}; use anthropic, openai or ollama")
    return KeelgateTextModel(client, model, tracer=tracer)
