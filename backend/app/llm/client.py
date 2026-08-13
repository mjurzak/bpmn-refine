"""Centralized LLM client facade.

All LLM calls MUST go through this module; call sites never import a provider directly.
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
    resolved_provider = provider or settings.llm_provider
    honored, unsupported, honored_reasoning = _resolve_controls(
        adapter, max_tokens, temperature, seed, reasoning_effort
    )
    started_at = utc_now()
    started = perf_counter()
    try:
        result = await adapter.complete(
            prompt=prompt,
            system=system,
            model=resolved_model,
            max_tokens=max_tokens,
            reasoning_effort=honored_reasoning,
            **honored,
        )
    except Exception as exc:
        _append_trace(
            kind="complete",
            task=task,
            provider=resolved_provider,
            provider_version=getattr(adapter, "harness_version", None),
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
        provider=resolved_provider,
        provider_version=getattr(adapter, "harness_version", None),
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
    resolved_provider = provider or settings.llm_provider
    honored, unsupported, honored_reasoning = _resolve_controls(
        adapter, max_tokens, temperature, seed, reasoning_effort
    )
    started_at = utc_now()
    started = perf_counter()
    try:
        result = await adapter.complete_with_history(
            messages=messages,
            system=system,
            model=resolved_model,
            max_tokens=max_tokens,
            reasoning_effort=honored_reasoning,
            **honored,
        )
    except Exception as exc:
        _append_trace(
            kind="complete_with_history",
            task=task,
            provider=resolved_provider,
            provider_version=getattr(adapter, "harness_version", None),
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
        provider=resolved_provider,
        provider_version=getattr(adapter, "harness_version", None),
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

    provider: name registered in llm/registry.py; defaults to settings.llm_provider.
    model:    model ID for the chosen provider; defaults to settings.llm_fast_model.
    """
    resolved_model = model or settings.llm_fast_model
    adapter = get_provider(provider)
    resolved_provider = provider or settings.llm_provider
    honored, unsupported, honored_reasoning = _resolve_controls(
        adapter, max_tokens, temperature, seed, reasoning_effort
    )
    started_at = utc_now()
    started = perf_counter()
    try:
        result = await adapter.complete_structured(
            prompt=prompt,
            system=system,
            model=resolved_model,
            schema=schema,
            max_tokens=max_tokens,
            reasoning_effort=honored_reasoning,
            **honored,
        )
    except Exception as exc:
        _append_trace(
            kind="complete_structured",
            task=task,
            provider=resolved_provider,
            provider_version=getattr(adapter, "harness_version", None),
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
        provider=resolved_provider,
        provider_version=getattr(adapter, "harness_version", None),
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
    resolved_provider = provider or settings.llm_provider
    honored, unsupported, honored_reasoning = _resolve_controls(
        adapter, max_tokens, temperature, seed, reasoning_effort
    )
    started_at = utc_now()
    started = perf_counter()
    try:
        result = await adapter.complete_structured_with_history(
            messages=messages,
            system=system,
            model=resolved_model,
            schema=schema,
            max_tokens=max_tokens,
            reasoning_effort=honored_reasoning,
            **honored,
        )
    except Exception as exc:
        _append_trace(
            kind="complete_structured_with_history",
            task=task,
            provider=resolved_provider,
            provider_version=getattr(adapter, "harness_version", None),
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
        provider=resolved_provider,
        provider_version=getattr(adapter, "harness_version", None),
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
    """accept either the response envelope or a bare string (test doubles), reporting no usage for the latter"""
    if isinstance(result, LlmResponse):
        return result.text, result.usage
    return result, None


def _resolve_controls(
    adapter: Any,
    max_tokens: int | None,
    temperature: float | None,
    seed: int | None,
    reasoning_effort: str | None,
) -> tuple[dict[str, Any], list[str], str | None]:
    """Split requested controls into forwarded and unsupported controls."""
    honored: dict[str, Any] = {}
    unsupported: list[str] = []

    if max_tokens is not None and not getattr(adapter, "supports_max_tokens", True):
        unsupported.append("max_tokens")
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

    honored_reasoning = reasoning_effort
    if reasoning_effort and not getattr(adapter, "supports_reasoning_effort", True):
        unsupported.append("reasoning_effort")
        honored_reasoning = None

    return honored, unsupported, honored_reasoning


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
    provider_version: str | None,
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
            provider_version=provider_version,
            model=model,
            reasoning_effort=reasoning_effort,
            effective_reasoning_effort=(
                None
                if "reasoning_effort" in unsupported_controls
                else reasoning_effort
            ),
            max_tokens=max_tokens,
            effective_max_tokens=(
                None if "max_tokens" in unsupported_controls else max_tokens
            ),
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
