"""Mocked focused coverage for the holistic tier-3 validation ablation."""

from __future__ import annotations

import json

import pytest

from app.experiments import ExperimentConfig, LlmValidationScope, TiersEnabled
from app.model.schema import (
    BpmnDiagram,
    BpmnProcess,
    FlowNode,
    FlowNodeType,
    SequenceFlow,
)
from app.services import validation as validation_service
from app.services.validation import (
    HolisticCategory,
    HolisticFinding,
    _HOLISTIC_SCHEMA,
    _normalise_holistic_findings,
    validate_diagram,
    validate_prompt_path,
)
from app.validation.rules import Severity, ValidationTier


def _diagram() -> BpmnDiagram:
    return BpmnDiagram(
        definitions_id="defs_1",
        processes=[
            BpmnProcess(
                id="Process_1",
                flow_nodes=[
                    FlowNode(id="start_1", type=FlowNodeType.START_EVENT),
                    FlowNode(id="task_1", type=FlowNodeType.TASK, name="Do it"),
                    FlowNode(id="end_1", type=FlowNodeType.END_EVENT),
                ],
                sequence_flows=[
                    SequenceFlow(id="sf_1", source_ref="start_1", target_ref="task_1"),
                    SequenceFlow(id="sf_2", source_ref="task_1", target_ref="end_1"),
                ],
            )
        ],
    )


def _finding(category: HolisticCategory, refs: list[str]) -> HolisticFinding:
    return HolisticFinding(
        category=category,
        severity=Severity.ERROR,
        message=f"Synthetic {category.value}.",
        element_refs=refs,
    )


def _diagram_without_start() -> BpmnDiagram:
    diagram = _diagram()
    diagram.processes[0].flow_nodes = [
        node
        for node in diagram.processes[0].flow_nodes
        if node.type != FlowNodeType.START_EVENT
    ]
    diagram.processes[0].sequence_flows = [
        flow
        for flow in diagram.processes[0].sequence_flows
        if flow.source_ref != "start_1"
    ]
    return BpmnDiagram.model_validate(diagram.model_dump(mode="json"))


def test_scope_is_compatible_and_closed():
    assert ExperimentConfig().llm_validation_scope == LlmValidationScope.SEMANTIC
    assert ExperimentConfig.model_validate(
        {"llm_validation_scope": "holistic"}
    ).llm_validation_scope == LlmValidationScope.HOLISTIC
    with pytest.raises(ValueError):
        ExperimentConfig.model_validate({"llm_validation_scope": "other"})


def test_holistic_schema_and_prompt_are_selected():
    config = ExperimentConfig(llm_validation_scope=LlmValidationScope.HOLISTIC)
    assert validate_prompt_path(config).name == "validate_holistic.txt"
    assert validate_prompt_path(ExperimentConfig()).name == "validate.txt"
    categories = _HOLISTIC_SCHEMA["$defs"]["HolisticCategory"]["enum"]
    assert set(categories) == {category.value for category in HolisticCategory}
    assert "missing_start_event" in categories
    assert "unwanted_action" in categories


def test_holistic_normalisation_stamps_ids_and_filters_references():
    issues = _normalise_holistic_findings(
        [
            _finding(
                HolisticCategory.DANGLING_REFERENCE,
                ["sf_1", "ghost", "task_1"],
            )
        ],
        _diagram(),
    )
    assert issues[0].rule_id == "llm:dangling_reference"
    assert issues[0].element_refs == ["sf_1", "task_1"]
    assert issues[0].element_id == "sf_1"
    assert issues[0].tier == ValidationTier.TIER3
    assert issues[0].source == "llm"


@pytest.mark.asyncio
async def test_holistic_uses_structured_schema_and_retains_overlap(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return {
            "description": "Two findings.",
            "result": {
                "findings": [
                    _finding(HolisticCategory.MISSING_START_EVENT, []).model_dump(
                        mode="json"
                    ),
                    _finding(HolisticCategory.MISSING_STEP, ["ghost"]).model_dump(
                        mode="json"
                    ),
                ]
            },
        }

    monkeypatch.setattr(
        validation_service.llm_client,
        "complete_structured",
        fake_complete_structured,
    )
    config = ExperimentConfig(
        llm_validation_scope=LlmValidationScope.HOLISTIC,
        tiers_enabled=TiersEnabled(t1=True, t2=False, t3=True),
    )
    result = await validate_diagram(_diagram_without_start(), config=config)

    assert captured["schema"] == _HOLISTIC_SCHEMA
    assert "missing_start_event" in str(captured["system"])
    payload = json.loads(captured["prompt"])
    assert [issue["rule_id"] for issue in payload["existing_issues"]] == ["R001"]
    assert [issue.rule_id for issue in result.semantic_issues] == [
        "llm:missing_start_event",
        "llm:missing_step",
    ]
    assert result.issues[0].rule_id == "R001"
    assert result.semantic_issues[0].element_refs == []
    assert result.semantic_issues[1].element_refs == []
