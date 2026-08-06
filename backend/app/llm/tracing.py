"""In-request LLM trace collection. Traces are request-scoped and in-memory only, never persisted."""

from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.llm.usage import TokenUsage, UsageTotals, total_usage


class LlmTrace(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: Literal[
        "complete",
        "complete_with_history",
        "complete_structured",
        "complete_structured_with_history",
    ]
    # logical task, independent of the call shape
    task: str | None = None
    provider: str | None = None
    model: str
    reasoning_effort: str | None = None
    max_tokens: int
    temperature: float | None = None
    seed: int | None = None
    # controls listed here were requested but never reached the provider
    unsupported_controls: list[str] = Field(default_factory=list)
    started_at: datetime
    duration_ms: int
    # `None` means the provider reported no usage, not that the call was free
    usage: TokenUsage | None = None
    system: str | None = None
    prompt: str | None = None
    messages: list[dict[str, Any]] | None = None
    schema_payload: dict[str, Any] | None = Field(default=None, alias="schema")
    output: str | None = None
    error: str | None = None


_traces: ContextVar[list[LlmTrace] | None] = ContextVar("llm_traces", default=None)


def start_trace_context() -> Token[list[LlmTrace] | None]:
    return _traces.set([])


def reset_trace_context(token: Token[list[LlmTrace] | None]) -> None:
    _traces.reset(token)


def append_trace(trace: LlmTrace) -> None:
    active_traces = _traces.get()
    if active_traces is not None:
        active_traces.append(trace)


def get_traces() -> list[LlmTrace]:
    return list(_traces.get() or [])


NO_MODEL_CALLED = "none"


def models_called(traces: Iterable[LlmTrace]) -> str:
    """the models a run actually addressed, in first-seen order"""
    
    seen: list[str] = []
    for trace in traces:
        if trace.model not in seen:
            seen.append(trace.model)
    return ", ".join(seen) if seen else NO_MODEL_CALLED


def trace_usage(traces: Iterable[LlmTrace]) -> UsageTotals:
    """token totals across a set of traces, including how many reported none"""
    return total_usage(trace.usage for trace in traces)


def utc_now() -> datetime:
    return datetime.now(UTC)
