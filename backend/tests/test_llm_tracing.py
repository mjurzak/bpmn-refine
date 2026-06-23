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
            reasoning_effort="low",
        )
        traces = get_traces()
    finally:
        reset_trace_context(token)

    assert result == "model output"
    assert len(traces) == 1
    trace = traces[0]
    assert trace.kind == "complete"
    assert trace.provider == "trace-provider"
    assert trace.model == "trace-model"
    assert trace.reasoning_effort == "low"
    assert trace.system == "system prompt"
    assert trace.prompt == "user prompt"
    assert trace.output == "model output"
    assert trace.error is None
    assert trace.duration_ms >= 0
