"""LLM provider protocol — the contract every provider must satisfy.

Adding a new provider means implementing this protocol and registering it via
llm/registry.py.  No other code needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.llm.usage import TokenUsage

# Sampling controls a provider may or may not accept. Declared per class rather
# than discovered per call, because an evaluation record has to say whether a
# control executed — and a provider that silently ignores `seed` is
# indistinguishable at the API boundary from one that honoured it.
SUPPORTS_TEMPERATURE = "temperature"
SUPPORTS_SEED = "seed"


@dataclass(frozen=True)
class LlmResponse:
    """A provider's answer plus what it cost.

    Adapters used to return the text alone, which threw away the only report of
    token consumption the API ever makes. Usage is optional rather than required
    so that a provider (or a test double) that cannot report it stays a valid
    implementation — the client facade records the absence instead of guessing a
    number.
    """

    text: str
    usage: TokenUsage | None = None


@runtime_checkable
class LLMProvider(Protocol):
    #: sampling controls this provider forwards; anything absent is recorded as
    #: unsupported on the trace instead of being reported as if it had applied
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
        """Schema-constrained completion.

        `schema` is a JSON schema describing the required response object.  Each
        provider maps it to its native structured-output mechanism and returns a
        JSON string that validates against the schema.  The caller is responsible
        for parsing (the client facade does this and returns the decoded object).
        """
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
