"""In-request LLM trace collection.

Traces are intentionally request-scoped and in-memory only. API routes can
return them to the frontend for transparency without persisting raw prompts.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class LlmTrace(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: Literal["complete", "complete_with_history", "complete_structured"]
    provider: str | None = None
    model: str
    reasoning_effort: str | None = None
    max_tokens: int
    started_at: datetime
    duration_ms: int
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


def utc_now() -> datetime:
    return datetime.now(UTC)
