from app.llm import client as llm_client
from app.llm.tracing import get_traces, reset_trace_context, start_trace_context


async def test_complete_records_prompt_output_and_timing(monkeypatch):
    class FakeProvider:
        async def complete(self, **kwargs):
            return "model output"

    monkeypatch.setattr(llm_client, "get_provider", lambda provider=None: FakeProvider())

    token = start_trace_context()
    try:
        result = await llm_client.complete(
            prompt="user prompt",
            system="system prompt",
            model="trace-model",
            provider="trace-provider",
            task="semantic_validation",
            reasoning_effort="low",
        )
        traces = get_traces()
    finally:
        reset_trace_context(token)

    assert result == "model output"
    assert len(traces) == 1
    trace = traces[0]
    assert trace.kind == "complete"
    assert trace.task == "semantic_validation"
    assert trace.provider == "trace-provider"
    assert trace.model == "trace-model"
    assert trace.reasoning_effort == "low"
    assert trace.system == "system prompt"
    assert trace.prompt == "user prompt"
    assert trace.output == "model output"
    assert trace.error is None
    assert trace.duration_ms >= 0


async def test_structured_history_trace_keeps_schema_and_raw_output(monkeypatch):
    schema = {
        "type": "object",
        "properties": {"description": {"type": "string"}},
        "required": ["description"],
        "additionalProperties": False,
    }

    class FakeProvider:
        async def complete_structured_with_history(self, **kwargs):
            return '{"description":"ok"}'

    monkeypatch.setattr(llm_client, "get_provider", lambda provider=None: FakeProvider())

    token = start_trace_context()
    try:
        result = await llm_client.complete_structured_with_history(
            messages=[{"role": "user", "content": "question"}],
            schema=schema,
            provider="trace-provider",
        )
        traces = get_traces()
    finally:
        reset_trace_context(token)

    assert result == {"description": "ok"}
    assert traces[0].kind == "complete_structured_with_history"
    assert traces[0].schema_payload == schema
    assert traces[0].output == '{"description":"ok"}'
