import json

from app.experiments import ExperimentConfig, IrFormat, RepairMode
from app.services import chat as chat_service
from app.services import repair as repair_service
from app.services.ir_payload import diagram_payload_text
from app.services.repair import repair_diagram
from app.services.validation import validate_diagram
from app.validation.rules import Severity, ValidationIssue
from tests.converter_cases import canonical_full_diagram


async def test_semantic_validation_prompt_uses_selected_ir_format(monkeypatch):
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return {"description": "No semantic issues.", "result": {"findings": []}}

    monkeypatch.setattr(
        "app.services.validation.llm_client.complete_structured",
        fake_complete_structured,
    )

    await validate_diagram(
        canonical_full_diagram(),
        include_semantic=True,
        config=ExperimentConfig(ir_format=IrFormat.YAML),
        reference_description="The clerk reviews the application.",
    )

    prompt = json.loads(captured["prompt"])
    assert prompt["ir_format"] == "yaml"
    assert isinstance(prompt["diagram"], str)
    assert "definitions_id: definitions_1" in prompt["diagram"]
    assert prompt["reference_description"] == (
        "L1: The clerk reviews the application."
    )


async def test_regen_repair_parses_selected_ir_format(monkeypatch):
    diagram = canonical_full_diagram()
    config = ExperimentConfig(
        ir_format=IrFormat.COMPACT_JSON,
        repair_mode=RepairMode.REGEN,
    )
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return {
            "description": "No repair needed.",
            "result": {
                "ir": diagram_payload_text(diagram, config),
                "unresolved": [],
            },
        }

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    result = await repair_diagram(
        diagram,
        issues=[
            ValidationIssue(
                rule_id="R999",
                severity=Severity.ERROR,
                message="Synthetic issue.",
            )
        ],
        config=config,
        snapshot=False,
    )

    prompt = json.loads(captured["prompt"])
    assert prompt["ir_format"] == "compact_json"
    assert isinstance(prompt["diagram"], str)
    assert "definitions_id" not in prompt["diagram"]
    assert result.repaired_diagram == diagram


async def test_chat_context_and_reply_use_selected_ir_format(monkeypatch):
    diagram = canonical_full_diagram()
    config = ExperimentConfig(ir_format=IrFormat.MERMAID)
    captured = {}

    async def fake_complete_structured_with_history(**kwargs):
        captured.update(kwargs)
        return {
            "description": "Updated diagram.",
            "result": {"diagram": diagram_payload_text(diagram, config)},
        }

    monkeypatch.setattr(
        chat_service.llm_client,
        "complete_structured_with_history",
        fake_complete_structured_with_history,
    )

    result = await chat_service.chat_diagram(
        messages=[chat_service.ChatMessage(role="user", content="Please refine it.")],
        diagram=diagram,
        config=config,
    )

    first_message = captured["messages"][0]["content"]
    assert "Current diagram (mermaid):" in first_message
    assert "```mermaid" in first_message
    assert "flowchart TD" in first_message
    assert result.reply == "Updated diagram."
    assert result.updated_diagram == diagram


async def test_chat_reprompts_a_schema_valid_but_invalid_diagram(monkeypatch):
    diagram = canonical_full_diagram()
    calls = []

    async def fake_complete_structured_with_history(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "description": "Broken proposal.",
                "result": {
                    "diagram": (
                        '{"definitions_id":"defs","processes":[{"id":"proc",'
                        '"flow_nodes":[{"id":"dup","type":"task"},'
                        '{"id":"dup","type":"task"}],"sequence_flows":[]}]}'
                    )
                },
            }
        return {
            "description": "Corrected proposal.",
            "result": {"diagram": diagram.model_dump_json()},
        }

    monkeypatch.setattr(
        chat_service.llm_client,
        "complete_structured_with_history",
        fake_complete_structured_with_history,
    )

    result = await chat_service.chat_diagram(
        messages=[chat_service.ChatMessage(role="user", content="Refine it.")],
        diagram=diagram,
        config=ExperimentConfig(),
        snapshot_changes=False,
    )

    assert len(calls) == 2
    assert "previous response was rejected" in calls[1]["messages"][-1]["content"]
    assert result.reply == "Corrected proposal."
    assert result.updated_diagram == diagram
