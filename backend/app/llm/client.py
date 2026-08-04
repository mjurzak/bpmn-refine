"""Centralized LLM client facade.

All LLM calls in the application MUST go through this module.
The actual work is delegated to the provider selected by LLM_PROVIDER in settings.

Call sites use the completion functions here and never import a provider directly.
"""
from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Literal

from app.core.config import settings
from app.llm.protocol import LlmResponse
from app.llm.registry import get_provider
from app.llm.tracing import LlmTrace, append_trace, utc_now
from app.llm.usage import TokenUsage


async def complete(
    prompt: str,
    system: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int = 4096,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    seed: int | None = None,
    task: str | None = None,
) -> str:
    """Single-turn completion.

    provider: name registered in llm/registry.py; defaults to settings.llm_provider.
    model:    model ID for the chosen provider; defaults to settings.llm_fast_model.
    """
    resolved_model = model or settings.llm_fast_model
    adapter = get_provider(provider)
    honored, unsupported = _resolve_sampling(adapter, temperature, seed)
    started_at = utc_now()
    started = perf_counter()
    try:
        result = await adapter.complete(
            prompt=prompt,
            system=system,
            model=resolved_model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            **honored,
        )
    except Exception as exc:
        _append_trace(
            kind="complete",
            task=task,
            provider=provider,
            model=resolved_model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            temperature=honored.get("temperature"),
            seed=honored.get("seed"),
            unsupported_controls=unsupported,
            started_at=started_at,
            started=started,
            system=system,
            prompt=prompt,
            error=str(exc),
        )
        raise
    output, usage = _unpack(result)
    _append_trace(
        kind="complete",
        task=task,
        provider=provider,
        model=resolved_model,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        temperature=honored.get("temperature"),
        seed=honored.get("seed"),
        unsupported_controls=unsupported,
        started_at=started_at,
        started=started,
        system=system,
        prompt=prompt,
        output=output,
        usage=usage,
    )
    return output


async def complete_with_history(
    messages: list[dict],
    system: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int = 4096,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    seed: int | None = None,
    task: str | None = None,
) -> str:
    """Multi-turn completion.

    messages format: [{"role": "user"|"assistant", "content": "..."}]
    provider: name registered in llm/registry.py; defaults to settings.llm_provider.
    model:    model ID for the chosen provider; defaults to settings.llm_fast_model.
    """
    resolved_model = model or settings.llm_fast_model
    adapter = get_provider(provider)
    honored, unsupported = _resolve_sampling(adapter, temperature, seed)
    started_at = utc_now()
    started = perf_counter()
    try:
        result = await adapter.complete_with_history(
            messages=messages,
            system=system,
            model=resolved_model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            **honored,
        )
    except Exception as exc:
        _append_trace(
            kind="complete_with_history",
            task=task,
            provider=provider,
            model=resolved_model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            temperature=honored.get("temperature"),
            seed=honored.get("seed"),
            unsupported_controls=unsupported,
            started_at=started_at,
            started=started,
            system=system,
            messages=messages,
            error=str(exc),
        )
        raise
    output, usage = _unpack(result)
    _append_trace(
        kind="complete_with_history",
        task=task,
        provider=provider,
        model=resolved_model,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        temperature=honored.get("temperature"),
        seed=honored.get("seed"),
        unsupported_controls=unsupported,
        started_at=started_at,
        started=started,
        system=system,
        messages=messages,
        output=output,
        usage=usage,
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
    temperature: float | None = None,
    seed: int | None = None,
    task: str | None = None,
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
    adapter = get_provider(provider)
    honored, unsupported = _resolve_sampling(adapter, temperature, seed)
    started_at = utc_now()
    started = perf_counter()
    try:
        result = await adapter.complete_structured(
            prompt=prompt,
            system=system,
            model=resolved_model,
            schema=schema,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            **honored,
        )
    except Exception as exc:
        _append_trace(
            kind="complete_structured",
            task=task,
            provider=provider,
            model=resolved_model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            temperature=honored.get("temperature"),
            seed=honored.get("seed"),
            unsupported_controls=unsupported,
            started_at=started_at,
            started=started,
            system=system,
            prompt=prompt,
            schema=schema,
            error=str(exc),
        )
        raise
    raw, usage = _unpack(result)
    _append_trace(
        kind="complete_structured",
        task=task,
        provider=provider,
        model=resolved_model,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        temperature=honored.get("temperature"),
        seed=honored.get("seed"),
        unsupported_controls=unsupported,
        started_at=started_at,
        started=started,
        system=system,
        prompt=prompt,
        schema=schema,
        output=raw,
        usage=usage,
    )
    return json.loads(raw)


async def complete_structured_with_history(
    messages: list[dict],
    schema: dict[str, Any],
    system: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int = 4096,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    seed: int | None = None,
    task: str | None = None,
) -> Any:
    """Schema-constrained multi-turn completion, returning parsed JSON."""
    resolved_model = model or settings.llm_fast_model
    adapter = get_provider(provider)
    honored, unsupported = _resolve_sampling(adapter, temperature, seed)
    started_at = utc_now()
    started = perf_counter()
    try:
        result = await adapter.complete_structured_with_history(
            messages=messages,
            system=system,
            model=resolved_model,
            schema=schema,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            **honored,
        )
    except Exception as exc:
        _append_trace(
            kind="complete_structured_with_history",
            task=task,
            provider=provider,
            model=resolved_model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            temperature=honored.get("temperature"),
            seed=honored.get("seed"),
            unsupported_controls=unsupported,
            started_at=started_at,
            started=started,
            system=system,
            messages=messages,
            schema=schema,
            error=str(exc),
        )
        raise
    raw, usage = _unpack(result)
    _append_trace(
        kind="complete_structured_with_history",
        task=task,
        provider=provider,
        model=resolved_model,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        temperature=honored.get("temperature"),
        seed=honored.get("seed"),
        unsupported_controls=unsupported,
        started_at=started_at,
        started=started,
        system=system,
        messages=messages,
        schema=schema,
        output=raw,
        usage=usage,
    )
    return json.loads(raw)


def _unpack(result: LlmResponse | str) -> tuple[str, TokenUsage | None]:
    """accept either the response envelope or a bare string

    Adapters return `LlmResponse`; a bare string keeps an older test double
    working and simply reports no usage, counted as unknown cost, not free.
    """
    if isinstance(result, LlmResponse):
        return result.text, result.usage
    return result, None


def _resolve_sampling(
    adapter: Any,
    temperature: float | None,
    seed: int | None,
) -> tuple[dict[str, Any], list[str]]:
    """split the requested sampling controls into forwarded and dropped

    Providers differ in what they accept — Anthropic has no sampling seed — so
    asking up front lets the trace record which controls actually executed,
    rather than implying a seeded run the API never saw.
    """
    honored: dict[str, Any] = {}
    unsupported: list[str] = []

    if temperature is not None:
        if getattr(adapter, "supports_temperature", True):
            honored["temperature"] = temperature
        else:
            unsupported.append("temperature")
    if seed is not None:
        if getattr(adapter, "supports_seed", True):
            honored["seed"] = seed
        else:
            unsupported.append("seed")

    return honored, unsupported


def _append_trace(
    *,
    kind: Literal[
        "complete",
        "complete_with_history",
        "complete_structured",
        "complete_structured_with_history",
    ],
    task: str | None,
    provider: str | None,
    model: str,
    max_tokens: int,
    reasoning_effort: str | None,
    temperature: float | None = None,
    seed: int | None = None,
    unsupported_controls: list[str] | None = None,
    started_at,
    started: float,
    system: str | None = None,
    prompt: str | None = None,
    messages: list[dict] | None = None,
    schema: dict[str, Any] | None = None,
    output: str | None = None,
    usage: TokenUsage | None = None,
    error: str | None = None,
) -> None:
    append_trace(
        LlmTrace(
            kind=kind,
            task=task,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=seed,
            unsupported_controls=unsupported_controls or [],
            started_at=started_at,
            duration_ms=max(0, round((perf_counter() - started) * 1000)),
            system=system,
            prompt=prompt,
            messages=messages,
            schema=schema,
            output=output,
            usage=usage,
            error=error,
        )
    )
