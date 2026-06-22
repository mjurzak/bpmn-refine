"""LLM provider protocol — the contract every provider must satisfy.

Adding a new provider means implementing this protocol and registering it via
llm/registry.py.  No other code needs to change.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(
        self,
        prompt: str,
        system: str | None,
        model: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> str:
        """Single-turn completion.  Returns the response text."""
        ...

    async def complete_with_history(
        self,
        messages: list[dict],
        system: str | None,
        model: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> str:
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
    ) -> str:
        """Schema-constrained completion.

        `schema` is a JSON schema describing the required response object.  Each
        provider maps it to its native structured-output mechanism and returns a
        JSON string that validates against the schema.  The caller is responsible
        for parsing (the client facade does this and returns the decoded object).
        """
        ...
