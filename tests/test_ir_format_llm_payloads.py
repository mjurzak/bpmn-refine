import json

from app.experiments import ExperimentConfig
from app.services import chat as chat_service
from app.services import repair as repair_service
from app.services.ir_payload import diagram_payload_text
from app.services.repair import repair_diagram
from app.services.validation import validate_diagram
from app.validation.rules import Severity, ValidationIssue
from tests.converter_cases import canonical_full_diagram


async def test_semantic_validation_prompt_uses_selected_ir_format(monkeypatch):
    captured = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        return "[]"

    monkeypatch.setattr("app.services.validation.llm_client.complete", fake_complete)

    await validate_diagram(
        canonical_full_diagram(),
        include_semantic=True,
        config=ExperimentConfig(ir_format="yaml"),
    )

    prompt = json.loads(captured["prompt"])
    assert prompt["ir_format"] == "yaml"
    assert isinstance(prompt["diagram"], str)
    assert "definitions_id: definitions_1" in prompt["diagram"]


async def test_regen_repair_parses_selected_ir_format(monkeypatch):
    diagram = canonical_full_diagram()
    config = ExperimentConfig(ir_format="compact_json", repair_mode="regen")
    captured = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        return json.dumps(
            {
                "ir": diagram_payload_text(diagram, config),
                "unresolved": [],
            }
        )

    monkeypatch.setattr(repair_service.llm_client, "complete", fake_complete)

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
    config = ExperimentConfig(ir_format="mermaid")
    captured = {}

    async def fake_complete_with_history(**kwargs):
        captured.update(kwargs)
        return f"Updated diagram:\n```mermaid\n{diagram_payload_text(diagram, config)}```"

    monkeypatch.setattr(
        chat_service.llm_client,
        "complete_with_history",
        fake_complete_with_history,
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
    assert result.updated_diagram == diagram
