"""Structured-outputs facade and per-provider request construction, against mocked SDKs."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.llm import client as llm_client
from app.llm.envelope import LlmResponseEnvelope
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.ollama import OllamaProvider
from app.llm.providers.openai import OpenAIProvider
from app.llm.schema import inline_defs, strict_json_schema
from app.repair.ops import AtomicEditOpsResult
from app.services.chat import _CHAT_SCHEMA
from app.services.repair import (
    _ATOMIC_OPS_SCHEMA,
    _RAW_XML_SCHEMA,
    _REGENERATION_SCHEMA,
)
from app.services.validation import _SEMANTIC_SCHEMA


_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}


# ----- schema helpers -------------------------------------------------------

def test_strict_schema_marks_objects_closed_and_required():
    schema = strict_json_schema(AtomicEditOpsResult)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["ops"]
    add_node = schema["$defs"]["AddNodeOp"]
    assert add_node["additionalProperties"] is False
    # every property is required, including the ones with defaults
    assert set(add_node["required"]) == set(add_node["properties"].keys())
    add_flow = schema["$defs"]["AddFlowOp"]
    assert "process_id" in add_flow["required"]
    assert set(add_flow["required"]) == set(add_flow["properties"].keys())


def test_strict_schema_rewrites_oneof_to_anyof():
    schema = strict_json_schema(AtomicEditOpsResult)
    dumped = json.dumps(schema)
    assert "oneOf" not in dumped
    assert "anyOf" in dumped


def test_inline_defs_removes_refs():
    schema = strict_json_schema(AtomicEditOpsResult)
    inlined = inline_defs(schema)
    assert "$defs" not in inlined
    assert "$ref" not in json.dumps(inlined)


def test_common_envelope_rejects_blank_descriptions():
    envelope = LlmResponseEnvelope[AtomicEditOpsResult]

    with pytest.raises(ValidationError, match="description must not be blank"):
        envelope.model_validate(
            {"description": " \n\t", "result": {"ops": []}}
        )


@pytest.mark.parametrize(
    ("schema", "result_model"),
    [
        (_SEMANTIC_SCHEMA, "SemanticFindings"),
        (_ATOMIC_OPS_SCHEMA, "AtomicEditOpsResult"),
        (_REGENERATION_SCHEMA, "RegenerationResult"),
        (_CHAT_SCHEMA, "ChatMachineResult"),
        (_RAW_XML_SCHEMA, "RawXmlResult"),
    ],
)
def test_every_task_schema_uses_the_common_outer_contract(schema, result_model):
    assert schema["required"] == ["description", "result"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["result"]["$ref"] == f"#/$defs/{result_model}"


# ----- Anthropic ------------------------------------------------------------

async def test_anthropic_structured_sends_output_config_and_parses():
    provider = AnthropicProvider(api_key="x")
    provider._client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"name": "ok"}')]
        )
    )

    response = await provider.complete_structured(
        prompt="p",
        system="s",
        model="m",
        schema=_SCHEMA,
        reasoning_effort="medium",
        max_tokens=1234,
    )

    kwargs = provider._client.messages.create.call_args.kwargs
    assert kwargs["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": _SCHEMA},
    }
    assert "effort" not in kwargs
    assert kwargs["max_tokens"] == 1234
    assert kwargs["system"] == "s"
    assert json.loads(response.text) == {"name": "ok"}
    # the stub carries no usage block on purpose
    assert response.usage is None


async def test_anthropic_structured_history_uses_the_same_schema_contract():
    provider = AnthropicProvider(api_key="x")
    provider._client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"name": "ok"}')]
        )
    )

    await provider.complete_structured_with_history(
        messages=[{"role": "user", "content": "p"}],
        system="s",
        model="m",
        schema=_SCHEMA,
    )

    kwargs = provider._client.messages.create.call_args.kwargs
    assert kwargs["messages"] == [{"role": "user", "content": "p"}]
    assert kwargs["output_config"]["format"]["schema"] == _SCHEMA


# ----- OpenAI / Ollama ------------------------------------------------------

async def test_openai_structured_uses_strict_response_format():
    provider = OpenAIProvider(api_key="x")
    message = SimpleNamespace(content='{"name": "ok"}')
    provider._client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(choices=[SimpleNamespace(message=message)])
    )

    response = await provider.complete_structured(
        prompt="p", system=None, model="m", schema=_SCHEMA, max_tokens=1234
    )

    kwargs = provider._client.chat.completions.create.call_args.kwargs
    fmt = kwargs["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == _SCHEMA
    assert kwargs["max_completion_tokens"] == 1234
    assert "max_tokens" not in kwargs
    assert json.loads(response.text) == {"name": "ok"}


async def test_ollama_disables_strict_flag():
    provider = OllamaProvider(base_url="http://localhost:11434")
    message = SimpleNamespace(content='{"name": "ok"}')
    provider._client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(choices=[SimpleNamespace(message=message)])
    )

    await provider.complete_structured(
        prompt="p", system=None, model="m", schema=_SCHEMA, max_tokens=1234
    )

    kwargs = provider._client.chat.completions.create.call_args.kwargs
    fmt = kwargs["response_format"]
    assert fmt["json_schema"]["strict"] is False
    assert kwargs["max_tokens"] == 1234
    assert "max_completion_tokens" not in kwargs


async def test_openai_structured_history_uses_response_format():
    provider = OpenAIProvider(api_key="x")
    message = SimpleNamespace(content='{"name": "ok"}')
    provider._client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(choices=[SimpleNamespace(message=message)])
    )

    await provider.complete_structured_with_history(
        messages=[{"role": "user", "content": "p"}],
        system="s",
        model="m",
        schema=_SCHEMA,
    )

    kwargs = provider._client.chat.completions.create.call_args.kwargs
    assert kwargs["messages"][0] == {"role": "system", "content": "s"}
    assert kwargs["response_format"]["json_schema"]["schema"] == _SCHEMA


# ----- Gemini ---------------------------------------------------------------

async def test_gemini_structured_inlines_schema():
    provider = GeminiProvider(api_key="x")
    provider._client.aio.models.generate_content = AsyncMock(
        return_value=SimpleNamespace(text='{"name": "ok"}')
    )

    nested = strict_json_schema(AtomicEditOpsResult)  # has $defs/$ref
    response = await provider.complete_structured(
        prompt="p",
        system="s",
        model="m",
        schema=nested,
        reasoning_effort="high",
    )

    config = provider._client.aio.models.generate_content.call_args.kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.thinking_config.thinking_level.value == "HIGH"
    # gemini cannot resolve refs, so the schema must arrive flattened
    assert "$ref" not in json.dumps(config.response_schema)
    assert json.loads(response.text) == {"name": "ok"}


async def test_gemini_structured_history_inlines_schema():
    provider = GeminiProvider(api_key="x")
    provider._client.aio.models.generate_content = AsyncMock(
        return_value=SimpleNamespace(text='{"name": "ok"}')
    )
    nested = strict_json_schema(AtomicEditOpsResult)

    await provider.complete_structured_with_history(
        messages=[{"role": "assistant", "content": "prior"}],
        system=None,
        model="m",
        schema=nested,
    )

    kwargs = provider._client.aio.models.generate_content.call_args.kwargs
    assert kwargs["contents"][0]["role"] == "model"
    assert "$ref" not in json.dumps(kwargs["config"].response_schema)


@pytest.mark.parametrize("reasoning_effort", ["none", "xhigh"])
async def test_gemini_rejects_reasoning_effort_without_a_native_equivalent(
    reasoning_effort,
):
    provider = GeminiProvider(api_key="x")

    with pytest.raises(ValueError, match="low, medium, high"):
        await provider.complete(
            prompt="p",
            system=None,
            model="m",
            reasoning_effort=reasoning_effort,
        )


# ----- client facade --------------------------------------------------------

async def test_client_facade_parses_provider_json(monkeypatch):
    fake = SimpleNamespace(complete_structured=AsyncMock(return_value='{"name": "ok"}'))
    monkeypatch.setattr("app.llm.client.get_provider", lambda provider=None: fake)

    result = await llm_client.complete_structured(prompt="p", schema=_SCHEMA, provider="x")

    assert result == {"name": "ok"}


async def test_client_facade_parses_structured_history_json(monkeypatch):
    fake = SimpleNamespace(
        complete_structured_with_history=AsyncMock(return_value='{"name": "ok"}')
    )
    monkeypatch.setattr("app.llm.client.get_provider", lambda provider=None: fake)

    result = await llm_client.complete_structured_with_history(
        messages=[{"role": "user", "content": "p"}],
        schema=_SCHEMA,
        provider="x",
    )

    assert result == {"name": "ok"}


# ----- atomic repair migration ----------------------------------------------

async def test_atomic_repair_uses_structured_outputs(monkeypatch):
    from app.experiments import ExperimentConfig, RepairMode
    from app.repair.ops import RenameNodeOp
    from app.services import repair as repair_service
    from app.validation.rules import Severity, ValidationIssue
    from tests.converter_cases import canonical_full_diagram

    diagram = canonical_full_diagram()
    config = ExperimentConfig(repair_mode=RepairMode.ATOMIC)
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return {
            "description": "Rename the task.",
            "result": {
                "ops": [
                    {"op": "rename_node", "id": "task_1", "new_name": "Renamed"}
                ]
            },
        }

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    ops = await repair_service.repair_with_edit_ops(
        diagram,
        issues=[
            ValidationIssue(
                rule_id="R999", severity=Severity.ERROR, message="Synthetic issue."
            )
        ],
        config=config,
    )

    assert captured["schema"]["additionalProperties"] is False
    assert captured["schema"]["required"] == ["description", "result"]
    assert len(ops) == 1
    assert isinstance(ops[0], RenameNodeOp)
    assert ops[0].new_name == "Renamed"
