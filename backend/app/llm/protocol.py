"""LLM provider protocol. A new provider implements this and registers in llm/registry.py."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.llm.usage import TokenUsage

# sampling controls a provider may or may not accept
SUPPORTS_TEMPERATURE = "temperature"
SUPPORTS_SEED = "seed"


@dataclass(frozen=True)
class LlmResponse:
    """A provider's answer plus what it cost. `usage` is None when the provider reported none."""

    text: str
    usage: TokenUsage | None = None


@runtime_checkable
class LLMProvider(Protocol):
    # sampling controls this provider forwards; the rest land on the trace as unsupported
    supports_temperature: bool
    supports_seed: bool

    async def complete(
        self,
        prompt: str,
        system: str | None,
        model: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> LlmResponse:
        """Single-turn completion.  Returns the response text and its usage."""
        ...

    async def complete_with_history(
        self,
        messages: list[dict],
        system: str | None,
        model: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> LlmResponse:
        """Multi-turn completion given a full message history.

        messages format: [{"role": "user"|"assistant", "content": "..."}]
        """
        ...

    async def complete_structured(
        self,
        prompt: str,
        system: str | None,
        model: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> LlmResponse:
        """Schema-constrained completion; returns an unparsed JSON string matching `schema`."""
        ...

    async def complete_structured_with_history(
        self,
        messages: list[dict],
        system: str | None,
        model: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> LlmResponse:
        """Schema-constrained multi-turn completion."""
        ...
