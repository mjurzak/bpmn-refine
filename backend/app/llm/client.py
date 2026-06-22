"""Centralized LLM client facade.

All LLM calls in the application MUST go through this module.
The actual work is delegated to the provider selected by LLM_PROVIDER in settings.

Call sites use complete() / complete_with_history() / complete_structured() and
never import a provider directly.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.llm.registry import get_provider


async def complete(
    prompt: str,
    system: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int = 4096,
    reasoning_effort: str | None = None,
) -> str:
    """Single-turn completion.

    provider: name registered in llm/registry.py; defaults to settings.llm_provider.
    model:    model ID for the chosen provider; defaults to settings.llm_fast_model.
    """
    return await get_provider(provider).complete(
        prompt=prompt,
        system=system,
        model=model or settings.llm_fast_model,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
    )


async def complete_with_history(
    messages: list[dict],
    system: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int = 4096,
    reasoning_effort: str | None = None,
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
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
    )


async def complete_structured(
    prompt: str,
    schema: dict[str, Any],
    system: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int = 4096,
    reasoning_effort: str | None = None,
) -> Any:
    """Schema-constrained completion, returning the parsed JSON object.

    `schema` is a JSON schema for the required response.  The selected provider
    maps it onto its native structured-output mechanism (Anthropic
    `output_config.format`, OpenAI/Ollama `response_format`, Gemini
    `responseSchema`); this facade decodes the returned JSON so call sites get a
    dict/list rather than a string.

    provider: name registered in llm/registry.py; defaults to settings.llm_provider.
    model:    model ID for the chosen provider; defaults to settings.llm_fast_model.
    """
    raw = await get_provider(provider).complete_structured(
        prompt=prompt,
        system=system,
        model=model or settings.llm_fast_model,
        schema=schema,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
    )
    return json.loads(raw)
