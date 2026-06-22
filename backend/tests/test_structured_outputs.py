"""Tests for the structured-outputs facade and per-provider request construction.

These mock each SDK client so no network/API key is needed — they assert that
the right native structured-output parameters are sent and that the constrained
JSON is parsed back correctly.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.llm import client as llm_client
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.ollama import OllamaProvider
from app.llm.providers.openai import OpenAIProvider
from app.llm.schema import inline_defs, strict_json_schema
from app.repair.ops import AtomicEditOpsResult


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
    # every declared property is required, including the ones with defaults
    assert set(add_node["required"]) == set(add_node["properties"].keys())


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


# ----- Anthropic ------------------------------------------------------------

async def test_anthropic_structured_sends_output_config_and_parses():
    provider = AnthropicProvider(api_key="x")
    provider._client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"name": "ok"}')]
        )
    )

    raw = await provider.complete_structured(
        prompt="p", system="s", model="m", schema=_SCHEMA
    )

    kwargs = provider._client.messages.create.call_args.kwargs
    assert kwargs["output_config"] == {
        "format": {"type": "json_schema", "schema": _SCHEMA}
    }
    assert kwargs["system"] == "s"
    assert json.loads(raw) == {"name": "ok"}


# ----- OpenAI / Ollama ------------------------------------------------------

async def test_openai_structured_uses_strict_response_format():
    provider = OpenAIProvider(api_key="x")
    message = SimpleNamespace(content='{"name": "ok"}')
    provider._client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(choices=[SimpleNamespace(message=message)])
    )

    raw = await provider.complete_structured(
        prompt="p", system=None, model="m", schema=_SCHEMA
    )

    fmt = provider._client.chat.completions.create.call_args.kwargs["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == _SCHEMA
    assert json.loads(raw) == {"name": "ok"}


async def test_ollama_disables_strict_flag():
    provider = OllamaProvider(base_url="http://localhost:11434")
    message = SimpleNamespace(content='{"name": "ok"}')
    provider._client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(choices=[SimpleNamespace(message=message)])
    )

    await provider.complete_structured(prompt="p", system=None, model="m", schema=_SCHEMA)

    fmt = provider._client.chat.completions.create.call_args.kwargs["response_format"]
    assert fmt["json_schema"]["strict"] is False


# ----- Gemini ---------------------------------------------------------------

async def test_gemini_structured_inlines_schema():
    provider = GeminiProvider(api_key="x")
    provider._client.aio.models.generate_content = AsyncMock(
        return_value=SimpleNamespace(text='{"name": "ok"}')
    )

    nested = strict_json_schema(AtomicEditOpsResult)  # has $defs/$ref
    raw = await provider.complete_structured(
        prompt="p", system="s", model="m", schema=nested
    )

    config = provider._client.aio.models.generate_content.call_args.kwargs["config"]
    assert config.response_mime_type == "application/json"
    # Gemini cannot resolve refs — the schema must arrive flattened
    assert "$ref" not in json.dumps(config.response_schema)
    assert json.loads(raw) == {"name": "ok"}


# ----- client facade --------------------------------------------------------

async def test_client_facade_parses_provider_json(monkeypatch):
    fake = SimpleNamespace(complete_structured=AsyncMock(return_value='{"name": "ok"}'))
    monkeypatch.setattr("app.llm.client.get_provider", lambda provider=None: fake)

    result = await llm_client.complete_structured(prompt="p", schema=_SCHEMA, provider="x")

    assert result == {"name": "ok"}


# ----- atomic repair migration ----------------------------------------------

async def test_atomic_repair_uses_structured_outputs(monkeypatch):
    from app.experiments import ExperimentConfig
    from app.repair.ops import RenameElementOp
    from app.services import repair as repair_service
    from app.validation.rules import Severity, ValidationIssue
    from tests.converter_cases import canonical_full_diagram

    diagram = canonical_full_diagram()
    config = ExperimentConfig(repair_mode="atomic")
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return {"ops": [{"op": "rename_element", "id": "task_1", "new_name": "Renamed"}]}

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

    # the strict EditOp schema is what gets sent, and the constrained reply
    # validates straight into typed ops
    assert captured["schema"]["additionalProperties"] is False
    assert len(ops) == 1
    assert isinstance(ops[0], RenameElementOp)
    assert ops[0].new_name == "Renamed"
