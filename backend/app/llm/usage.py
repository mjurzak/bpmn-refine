"""Token accounting for LLM calls.

Cost is one of the few quantities an evaluation can measure objectively, and it
is the one measurement that cannot be recovered afterwards: a provider reports
usage only in the response that carried it, so a call whose usage block was
dropped is unmeasurable forever. Every adapter therefore reads its own usage
block through this module.

Two conventions differ between providers and the difference matters to anything
that sums these numbers:

* **Anthropic** reports `input_tokens` *excluding* cache reads and writes, so the
  billed prompt size is `input_tokens + cached_input_tokens`.
* **OpenAI** (and Ollama's compatible endpoint) reports `prompt_tokens`
  *including* cached tokens, so `cached_input_tokens` is a subset, not an addend.
* **Gemini** follows the OpenAI convention: `prompt_token_count` is the total.

Rather than normalise these into one number and lose the distinction, each field
is stored as the provider reported it and `source` records which convention was
used. Nothing here invents a count: a field the provider did not report stays
`None`, because a missing count and a zero count mean different things and
collapsing them would quietly understate a total.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, computed_field


class TokenUsage(BaseModel):
    """what one provider call consumed, as that provider reported it"""

    input_tokens: int | None = None
    output_tokens: int | None = None
    # kept separate from `input_tokens` because a cached prompt token bills
    # differently — and because whether it is already counted in `input_tokens`
    # depends on `source`, see the module docstring
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    #: which provider's reporting convention produced these fields
    source: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int | None:
        """input plus output, or `None` when the provider reported neither"""
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


class UsageTotals(BaseModel):
    """summed usage across a set of calls

    `calls_missing_usage` is not decoration: without it a total reads as
    complete even when half the calls reported nothing, which is exactly how a
    cost comparison between two configurations turns into a comparison between
    two different sample sizes.
    """

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


# ---------------------------------------------------------------------------
# per-provider extraction
#
# All of these read defensively. SDK response objects gain and lose fields
# between releases, and a usage block that moved is a reason to record nothing
# for that call — never a reason to fail a repair the user is waiting on.
# ---------------------------------------------------------------------------


def _int(value: Any) -> int | None:
    # bool is an int subclass; a `True` slipping into a token count would be
    # silently summed as 1
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
    # source is specified for other OpenAI-compatible endpoints
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
