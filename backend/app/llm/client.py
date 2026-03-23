"""Centralized LLM client.

All LLM calls in the application MUST go through this module.  Never
instantiate anthropic.Anthropic directly elsewhere.
"""
from __future__ import annotations

import anthropic

from app.core.config import settings

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    """Return the shared Anthropic client (lazy-initialized)."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


async def complete(
    prompt: str,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
) -> str:
    """Send a single-turn completion and return the text response.

    Args:
        prompt: User message content.
        system: Optional system prompt.
        model: Override the model; defaults to the fast model from settings.
        max_tokens: Maximum tokens in the response.
    """
    client = get_client()
    selected_model = model or settings.llm_fast_model
    messages: list[dict] = [{"role": "user", "content": prompt}]

    kwargs: dict = {"model": selected_model, "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)
    return response.content[0].text


async def complete_with_history(
    messages: list[dict],
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
) -> str:
    """Multi-turn completion given a full message history.

    messages format: [{"role": "user"|"assistant", "content": "..."}]
    """
    client = get_client()
    selected_model = model or settings.llm_fast_model
    kwargs: dict = {"model": selected_model, "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)
    return response.content[0].text
