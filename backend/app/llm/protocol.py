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
    ) -> str:
        """Single-turn completion.  Returns the response text."""
        ...

    async def complete_with_history(
        self,
        messages: list[dict],
        system: str | None,
        model: str,
    ) -> str:
        """Multi-turn completion given a full message history.

        messages format: [{"role": "user"|"assistant", "content": "..."}]
        """
        ...
