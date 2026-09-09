"""Token accounting for LLM calls.

Providers disagree on what `input_tokens` includes, so `source` records the convention:

* Anthropic excludes cache reads/writes: billed prompt size is `input_tokens + cached_input_tokens`
* OpenAI, Ollama and Gemini include them: `cached_input_tokens` is a subset, not an addend

Fields are stored as reported; one the provider omitted stays `None`, never 0.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, computed_field


class TokenUsage(BaseModel):
    """what one provider call consumed, as that provider reported it"""

    input_tokens: int | None = None
    output_tokens: int | None = None
    # whether these are already inside `input_tokens` depends on `source`
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    # which provider's reporting convention produced these fields
    source: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int | None:
        """input plus output, or `None` when the provider reported neither"""
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


class UsageTotals(BaseModel):
    """summed usage across a set of calls; `calls_missing_usage` marks a partial total as partial"""

    calls: int = 0
    calls_missing_usage: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def complete(self) -> bool:
        """whether every counted call reported usage"""
        return self.calls_missing_usage == 0


def total_usage(usages: Iterable[TokenUsage | None]) -> UsageTotals:
    """add up per-call usage, counting the calls that reported none"""
    totals = UsageTotals()
    for usage in usages:
        totals.calls += 1
        if usage is None or usage.total_tokens is None:
            totals.calls_missing_usage += 1
            continue
        totals.input_tokens += usage.input_tokens or 0
        totals.output_tokens += usage.output_tokens or 0
        totals.total_tokens += usage.total_tokens
        totals.cached_input_tokens += usage.cached_input_tokens or 0
        totals.reasoning_tokens += usage.reasoning_tokens or 0
    return totals


# per-provider extraction. read defensively: SDK fields move between releases,
# and a moved field should record nothing rather than fail the call


def _int(value: Any) -> int | None:
    # bool is an int subclass, so a stray `True` would be summed as 1
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _sum_present(*values: int | None) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def from_anthropic(response: Any) -> TokenUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return TokenUsage(
        input_tokens=_int(getattr(usage, "input_tokens", None)),
        output_tokens=_int(getattr(usage, "output_tokens", None)),
        # both cache counters are prompt tokens that `input_tokens` excludes
        cached_input_tokens=_sum_present(
            _int(getattr(usage, "cache_read_input_tokens", None)),
            _int(getattr(usage, "cache_creation_input_tokens", None)),
        ),
        source="anthropic",
    )


def from_openai(response: Any, source: str = "openai") -> TokenUsage | None:
    # source is overridden by other OpenAI-compatible endpoints
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    return TokenUsage(
        input_tokens=_int(getattr(usage, "prompt_tokens", None)),
        output_tokens=_int(getattr(usage, "completion_tokens", None)),
        cached_input_tokens=_int(getattr(prompt_details, "cached_tokens", None)),
        reasoning_tokens=_int(getattr(completion_details, "reasoning_tokens", None)),
        source=source,
    )


def from_gemini(response: Any) -> TokenUsage | None:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    return TokenUsage(
        input_tokens=_int(getattr(usage, "prompt_token_count", None)),
        output_tokens=_int(getattr(usage, "candidates_token_count", None)),
        cached_input_tokens=_int(getattr(usage, "cached_content_token_count", None)),
        reasoning_tokens=_int(getattr(usage, "thoughts_token_count", None)),
        source="gemini",
    )


def from_cli(usage: Any, source: str) -> TokenUsage | None:
    """Read token counters from a CLI's JSON usage object when it reports them."""
    if not isinstance(usage, dict):
        return None

    def value(*names: str) -> int | None:
        for name in names:
            parsed = _int(usage.get(name))
            if parsed is not None:
                return parsed
        return None

    cached_input = value("cached_input_tokens", "cachedInputTokens")
    if cached_input is None:
        cached_input = _sum_present(
            value("cache_read_input_tokens", "cacheReadInputTokens"),
            value("cache_creation_input_tokens", "cacheCreationInputTokens"),
        )
    parsed = TokenUsage(
        input_tokens=value("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"),
        output_tokens=value(
            "output_tokens", "outputTokens", "completion_tokens", "completionTokens"
        ),
        cached_input_tokens=cached_input,
        reasoning_tokens=value(
            "reasoning_tokens",
            "reasoningTokens",
            "reasoning_output_tokens",
            "reasoningOutputTokens",
        ),
        source=source,
    )
    return parsed if parsed.total_tokens is not None else None
