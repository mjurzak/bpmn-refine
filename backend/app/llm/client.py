"""Centralized LLM client facade.

All LLM calls in the application MUST go through this module.
The actual work is delegated to the provider selected by LLM_PROVIDER in settings.

Call sites use complete() / complete_with_history() and never import a provider directly.
"""
from __future__ import annotations

from app.core.config import settings
from app.llm.registry import get_provider


async def complete(
    prompt: str,
    system: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int = 4096,
) -> str:
    """Single-turn completion.

    provider: name registered in llm/registry.py; defaults to settings.llm_provider.
    model:    model ID for the chosen provider; defaults to settings.llm_fast_model.
    """
    return await get_provider(provider).complete(
        prompt=prompt,
        system=system,
        model=model or settings.llm_fast_model,
    )


async def complete_with_history(
    messages: list[dict],
    system: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int = 4096,
) -> str:
    """Multi-turn completion.

    messages format: [{"role": "user"|"assistant", "content": "..."}]
    provider: name registered in llm/registry.py; defaults to settings.llm_provider.
    model:    model ID for the chosen provider; defaults to settings.llm_fast_model.
    """
    return await get_provider(provider).complete_with_history(
        messages=messages,
        system=system,
        model=model or settings.llm_fast_model,
    )
