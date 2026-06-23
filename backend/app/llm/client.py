"""Centralized LLM client facade.

All LLM calls in the application MUST go through this module.
The actual work is delegated to the provider selected by LLM_PROVIDER in settings.

Call sites use complete() / complete_with_history() / complete_structured() and
never import a provider directly.
"""
from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from app.core.config import settings
from app.llm.registry import get_provider
from app.llm.tracing import LlmTrace, append_trace, utc_now


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
    resolved_model = model or settings.llm_fast_model
    started_at = utc_now()
    started = perf_counter()
    try:
        output = await get_provider(provider).complete(
            prompt=prompt,
            system=system,
            model=resolved_model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
    except Exception as exc:
        _append_trace(
            kind="complete",
            provider=provider,
            model=resolved_model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            started_at=started_at,
            started=started,
            system=system,
            prompt=prompt,
            error=str(exc),
        )
        raise
    _append_trace(
        kind="complete",
        provider=provider,
        model=resolved_model,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        started_at=started_at,
        started=started,
        system=system,
        prompt=prompt,
        output=output,
    )
    return output


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
    resolved_model = model or settings.llm_fast_model
    started_at = utc_now()
    started = perf_counter()
    try:
        output = await get_provider(provider).complete_with_history(
            messages=messages,
            system=system,
            model=resolved_model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
    except Exception as exc:
        _append_trace(
            kind="complete_with_history",
            provider=provider,
            model=resolved_model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            started_at=started_at,
            started=started,
            system=system,
            messages=messages,
            error=str(exc),
        )
        raise
    _append_trace(
        kind="complete_with_history",
        provider=provider,
        model=resolved_model,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        started_at=started_at,
        started=started,
        system=system,
        messages=messages,
        output=output,
    )
    return output


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
    resolved_model = model or settings.llm_fast_model
    started_at = utc_now()
    started = perf_counter()
    try:
        raw = await get_provider(provider).complete_structured(
            prompt=prompt,
            system=system,
            model=resolved_model,
            schema=schema,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
    except Exception as exc:
        _append_trace(
            kind="complete_structured",
            provider=provider,
            model=resolved_model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            started_at=started_at,
            started=started,
            system=system,
            prompt=prompt,
            schema=schema,
            error=str(exc),
        )
        raise
    _append_trace(
        kind="complete_structured",
        provider=provider,
        model=resolved_model,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        started_at=started_at,
        started=started,
        system=system,
        prompt=prompt,
        schema=schema,
        output=raw,
    )
    return json.loads(raw)


def _append_trace(
    *,
    kind: str,
    provider: str | None,
    model: str,
    max_tokens: int,
    reasoning_effort: str | None,
    started_at,
    started: float,
    system: str | None = None,
    prompt: str | None = None,
    messages: list[dict] | None = None,
    schema: dict[str, Any] | None = None,
    output: str | None = None,
    error: str | None = None,
) -> None:
    append_trace(
        LlmTrace(
            kind=kind,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            started_at=started_at,
            duration_ms=max(0, round((perf_counter() - started) * 1000)),
            system=system,
            prompt=prompt,
            messages=messages,
            schema=schema,
            output=output,
            error=error,
        )
    )
