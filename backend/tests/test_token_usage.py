"""Tests for token accounting.

Usage is reported once, in the response that carried it. Every gap here is a
measurement that cannot be recovered after an experiment has run, so the cases
below cover both directions: that a reported count reaches the trace, and that
an unreported one is recorded as missing rather than as zero.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.llm import client as llm_client
from app.llm.protocol import LlmResponse
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.ollama import OllamaProvider
from app.llm.providers.openai import OpenAIProvider
from app.llm.tracing import (
    get_traces,
    reset_trace_context,
    start_trace_context,
    trace_usage,
)
from app.llm.usage import (
    TokenUsage,
    from_anthropic,
    from_gemini,
    from_openai,
    total_usage,
)


@pytest.fixture
def traces():
    token = start_trace_context()
    try:
        yield get_traces
    finally:
        reset_trace_context(token)


# ----- the model ------------------------------------------------------------


def test_total_is_none_when_the_provider_reported_nothing():
    """a missing count must not read as a free call"""
    assert TokenUsage().total_tokens is None


def test_total_treats_a_single_missing_side_as_zero():
    assert TokenUsage(input_tokens=10).total_tokens == 10


def test_total_tokens_is_serialised():
    """evaluation scripts read the dumped record, not the live object"""
    dumped = TokenUsage(input_tokens=3, output_tokens=4).model_dump()
    assert dumped["total_tokens"] == 7


def test_totals_count_calls_that_reported_no_usage():
    totals = total_usage(
        [TokenUsage(input_tokens=10, output_tokens=5), None, TokenUsage()]
    )
    assert totals.calls == 3
    assert totals.calls_missing_usage == 2
    assert totals.total_tokens == 15
    assert totals.complete is False


def test_totals_are_complete_when_every_call_reported():
    totals = total_usage([TokenUsage(input_tokens=1, output_tokens=1)] * 2)
    assert totals.complete is True
    assert totals.total_tokens == 4


# ----- per-provider extraction ----------------------------------------------


def test_anthropic_usage_keeps_cache_tokens_separate():
    """Anthropic excludes cache tokens from `input_tokens`, so they cannot merge"""
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=30,
            cache_creation_input_tokens=5,
        )
    )
    usage = from_anthropic(response)
    assert usage is not None
    assert usage.input_tokens == 100
    assert usage.cached_input_tokens == 35
    assert usage.source == "anthropic"


def test_openai_usage_reads_nested_detail_blocks():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=200,
            completion_tokens=50,
            prompt_tokens_details=SimpleNamespace(cached_tokens=64),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=40),
        )
    )
    usage = from_openai(response)
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (200, 50)
    assert usage.cached_input_tokens == 64
    assert usage.reasoning_tokens == 40


def test_gemini_usage_reads_its_own_field_names():
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=11,
            candidates_token_count=7,
            thoughts_token_count=3,
        )
    )
    usage = from_gemini(response)
    assert usage is not None
    assert usage.total_tokens == 18
    assert usage.reasoning_tokens == 3


@pytest.mark.parametrize("extract", [from_anthropic, from_openai, from_gemini])
def test_a_response_without_usage_yields_none(extract):
    """SDKs move fields between releases; that must not raise mid-repair"""
    assert extract(SimpleNamespace()) is None


def test_a_partial_usage_block_reports_only_what_was_present():
    usage = from_openai(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=9)))
    assert usage is not None
    assert usage.input_tokens == 9
    assert usage.output_tokens is None
    assert usage.reasoning_tokens is None


def test_a_bool_is_not_accepted_as_a_token_count():
    """bool subclasses int, and `True` would otherwise be summed as 1"""
    usage = from_openai(
        SimpleNamespace(usage=SimpleNamespace(prompt_tokens=True, completion_tokens=4))
    )
    assert usage is not None
    assert usage.input_tokens is None
    assert usage.output_tokens == 4


# ----- the adapters ---------------------------------------------------------


async def test_anthropic_adapter_returns_usage_with_the_text():
    provider = AnthropicProvider(api_key="x")
    provider._client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hello")],
            usage=SimpleNamespace(input_tokens=12, output_tokens=3),
        )
    )

    response = await provider.complete(prompt="p", system=None, model="m")

    assert isinstance(response, LlmResponse)
    assert response.text == "hello"
    assert response.usage is not None
    assert response.usage.total_tokens == 15


async def test_openai_adapter_returns_usage_with_the_text():
    provider = OpenAIProvider(api_key="x")
    provider._client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=2),
        )
    )

    response = await provider.complete(prompt="p", system=None, model="m")

    assert response.text == "hi"
    assert response.usage is not None
    assert response.usage.total_tokens == 10
    assert response.usage.source == "openai"


async def test_ollama_labels_its_usage_as_its_own():
    """a local tokenizer's counts are not comparable to a hosted provider's"""
    provider = OllamaProvider(base_url="http://localhost:11434")
    provider._client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=2),
        )
    )

    response = await provider.complete(prompt="p", system=None, model="m")

    assert response.usage is not None
    assert response.usage.source == "ollama"


async def test_gemini_adapter_returns_usage_with_the_text():
    provider = GeminiProvider(api_key="x")
    provider._client.aio.models.generate_content = AsyncMock(
        return_value=SimpleNamespace(
            text="hi",
            usage_metadata=SimpleNamespace(
                prompt_token_count=6, candidates_token_count=1
            ),
        )
    )

    response = await provider.complete(prompt="p", system=None, model="m")

    assert response.text == "hi"
    assert response.usage is not None
    assert response.usage.total_tokens == 7


# ----- the facade -----------------------------------------------------------


async def test_the_facade_records_usage_on_the_trace(monkeypatch, traces):
    fake = SimpleNamespace(
        complete=AsyncMock(
            return_value=LlmResponse("out", TokenUsage(input_tokens=40, output_tokens=9))
        )
    )
    monkeypatch.setattr("app.llm.client.get_provider", lambda provider=None: fake)

    output = await llm_client.complete(prompt="p", provider="x")

    assert output == "out"
    recorded = traces()
    assert len(recorded) == 1
    assert recorded[0].usage is not None
    assert recorded[0].usage.total_tokens == 49


async def test_the_facade_still_accepts_a_bare_string(monkeypatch, traces):
    """an out-of-tree provider on the older contract keeps working"""
    fake = SimpleNamespace(complete=AsyncMock(return_value="out"))
    monkeypatch.setattr("app.llm.client.get_provider", lambda provider=None: fake)

    output = await llm_client.complete(prompt="p", provider="x")

    assert output == "out"
    assert traces()[0].usage is None


async def test_structured_calls_record_usage(monkeypatch, traces):
    fake = SimpleNamespace(
        complete_structured=AsyncMock(
            return_value=LlmResponse(
                '{"ok": true}', TokenUsage(input_tokens=5, output_tokens=5)
            )
        )
    )
    monkeypatch.setattr("app.llm.client.get_provider", lambda provider=None: fake)

    parsed = await llm_client.complete_structured(prompt="p", schema={}, provider="x")

    assert parsed == {"ok": True}
    assert traces()[0].usage is not None
    assert traces()[0].usage.total_tokens == 10


async def test_a_failed_call_is_traced_without_usage(monkeypatch, traces):
    """the provider raised, so there is no usage block to read"""
    fake = SimpleNamespace(complete=AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr("app.llm.client.get_provider", lambda provider=None: fake)

    with pytest.raises(RuntimeError):
        await llm_client.complete(prompt="p", provider="x")

    recorded = traces()
    assert recorded[0].error == "boom"
    assert recorded[0].usage is None
    assert trace_usage(recorded).calls_missing_usage == 1


async def test_trace_usage_sums_a_multi_call_request(monkeypatch, traces):
    """a repair loop makes several calls; the run record needs their total"""
    fake = SimpleNamespace(
        complete=AsyncMock(
            side_effect=[
                LlmResponse("a", TokenUsage(input_tokens=10, output_tokens=1)),
                LlmResponse("b", TokenUsage(input_tokens=20, output_tokens=2)),
            ]
        )
    )
    monkeypatch.setattr("app.llm.client.get_provider", lambda provider=None: fake)

    await llm_client.complete(prompt="p", provider="x")
    await llm_client.complete(prompt="q", provider="x")

    totals = trace_usage(traces())
    assert totals.calls == 2
    assert totals.input_tokens == 30
    assert totals.output_tokens == 3
    assert totals.complete is True
