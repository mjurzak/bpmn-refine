from app.experiments import ExperimentConfig
from app.model.schema import BpmnDiagram, BpmnProcess, FlowNode, FlowNodeType, SequenceFlow
from app.services import validation as validation_service
from app.validation.checkers import checker_versions
from app.validation.rules import Severity, ValidationIssue


async def test_validate_diagram_merges_tier2_checker_issues(monkeypatch):
    async def fake_run_tier2_checkers(diagram, config):
        return [
            ValidationIssue(
                rule_id="woflan:soundness",
                severity=Severity.ERROR,
                message="Process can deadlock.",
                element_refs=["task_1"],
                source="woflan",
            )
        ]

    monkeypatch.setattr(
        validation_service,
        "run_tier2_checkers",
        fake_run_tier2_checkers,
    )

    result = await validation_service.validate_diagram(
        _minimal_valid_diagram(),
        config=ExperimentConfig(tiers_enabled={"t2": True}),
    )

    assert result.is_valid is False
    assert result.issues[0].rule_id == "woflan:soundness"
    assert result.issues[0].source == "woflan"
    assert result.issues[0].element_refs == ["task_1"]


def test_checker_versions_reports_enabled_selected_woflan():
    versions = checker_versions(
        ExperimentConfig(
            tiers_enabled={"t2": True},
            t2_tools=["woflan"],
        )
    )

    assert versions["woflan"].startswith("pm4py-")


def _minimal_valid_diagram() -> BpmnDiagram:
    start = FlowNode(id="start_1", type=FlowNodeType.START_EVENT, outgoing=["sf_1"])
    task = FlowNode(
        id="task_1",
        type=FlowNodeType.TASK,
        incoming=["sf_1"],
        outgoing=["sf_2"],
    )
    end = FlowNode(id="end_1", type=FlowNodeType.END_EVENT, incoming=["sf_2"])
    flows = [
        SequenceFlow(id="sf_1", source_ref="start_1", target_ref="task_1"),
        SequenceFlow(id="sf_2", source_ref="task_1", target_ref="end_1"),
    ]
    return BpmnDiagram(
        definitions_id="def_1",
        processes=[BpmnProcess(id="proc_1", flow_nodes=[start, task, end], sequence_flows=flows)],
    )
