import json

from app.experiments import ExperimentConfig
from app.model.schema import BpmnDiagram, BpmnProcess, FlowNode, FlowNodeType, SequenceFlow
from app.repair.ops import RenameElementOp
from app.services import repair as repair_service
from app.services.repair import RepairResult, dispatch_repair, repair_with_edit_ops
from app.validation.rules import Severity, ValidationIssue


async def test_dispatch_repair_prefers_quick_fix_over_llm():
    calls = []

    async def fake_repair(diagram, issues, **kwargs):
        calls.append(issues)
        return RepairResult(repaired_diagram=_minimal_valid_diagram())

    result = await dispatch_repair(
        _diagram_without_start(),
        issues=[
            ValidationIssue(
                rule_id="R001",
                severity=Severity.ERROR,
                message="Process has no start event.",
            )
        ],
        repair_fn=fake_repair,
    )

    assert result.iterations == 1
    assert result.converged is True
    assert result.remaining_issues == []
    assert [op.op for op in result.applied_ops] == ["add_node", "add_flow"]
    assert calls == []


async def test_dispatch_repair_uses_atomic_llm_ops_when_no_quick_fix_exists():
    calls = []

    async def fake_atomic_repair(diagram, issues, **kwargs):
        calls.append((diagram, issues, kwargs))
        return [RenameElementOp(id="task_1", new_name="Renamed task")]

    result = await dispatch_repair(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id="R999",
                severity=Severity.ERROR,
                message="Synthetic issue without a deterministic quick fix.",
                element_id="task_1",
            )
        ],
        atomic_repair_fn=fake_atomic_repair,
    )

    assert result.iterations == 1
    assert result.converged is True
    assert result.remaining_issues == []
    assert [op.op for op in result.applied_ops] == ["rename_element"]
    assert calls[0][1][0].rule_id == "R999"


async def test_dispatch_repair_regen_mode_uses_full_ir_replacement():
    calls = []

    async def fake_repair(diagram, issues, **kwargs):
        calls.append((diagram, issues, kwargs))
        return RepairResult(repaired_diagram=_minimal_valid_diagram())

    result = await dispatch_repair(
        _diagram_without_start(),
        issues=[
            ValidationIssue(
                rule_id="R001",
                severity=Severity.ERROR,
                message="Process has no start event.",
            )
        ],
        config=ExperimentConfig(repair_mode="regen"),
        repair_fn=fake_repair,
    )

    assert result.iterations == 1
    assert result.converged is True
    assert [op.op for op in result.applied_ops] == ["replace_diagram"]
    assert calls[0][1][0].rule_id == "R001"
    assert calls[0][2]["snapshot"] is False


async def test_dispatch_repair_respects_max_iteration_cap():
    calls = []

    async def fake_repair(diagram, issues, **kwargs):
        calls.append(issues[0].rule_id)
        return RepairResult(repaired_diagram=diagram)

    result = await dispatch_repair(
        _duplicate_id_diagram(),
        issues=[
            ValidationIssue(
                rule_id="R011",
                severity=Severity.ERROR,
                message="Duplicate element ID 'task_1' in process 'proc_1'.",
                element_id="task_1",
            )
        ],
        config=ExperimentConfig(repair_mode="regen", max_repair_iters=2),
        repair_fn=fake_repair,
    )

    assert result.iterations == 2
    assert result.converged is False
    assert calls == ["R011", "R011"]
    assert {issue.rule_id for issue in result.remaining_issues} >= {"R011"}
    assert [op.op for op in result.applied_ops] == ["replace_diagram", "replace_diagram"]


async def test_dispatch_repair_does_not_iterate_when_no_errors_remain():
    calls = []

    async def fake_repair(diagram, issues, **kwargs):
        calls.append(issues)
        return RepairResult(repaired_diagram=diagram)

    result = await dispatch_repair(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id="R007",
                severity=Severity.WARNING,
                message="Gateway has fewer than 2 outgoing flows.",
            )
        ],
        repair_fn=fake_repair,
    )

    assert result.iterations == 0
    assert result.converged is True
    assert result.remaining_issues[0].rule_id == "R007"
    assert calls == []


async def test_repair_with_edit_ops_parses_atomic_llm_output(monkeypatch):
    captured = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        return json.dumps(
            {
                "ops": [
                    {
                        "op": "rename_element",
                        "id": "task_1",
                        "new_name": "Renamed task",
                    }
                ],
                "unresolved": [],
            }
        )

    monkeypatch.setattr(repair_service.llm_client, "complete", fake_complete)

    ops = await repair_with_edit_ops(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id="R999",
                severity=Severity.ERROR,
                message="Synthetic atomic repair issue.",
                element_id="task_1",
            )
        ],
        config=ExperimentConfig(),
    )

    assert [op.op for op in ops] == ["rename_element"]
    assert "Available atomic EditOps" in captured["system"]
    assert "replace_diagram" not in captured["system"]
    assert json.loads(captured["prompt"])["repair_mode"] == "atomic"


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


def _diagram_without_start() -> BpmnDiagram:
    task = FlowNode(id="task_1", type=FlowNodeType.TASK, outgoing=["sf_1"])
    end = FlowNode(id="end_1", type=FlowNodeType.END_EVENT, incoming=["sf_1"])
    flow = SequenceFlow(id="sf_1", source_ref="task_1", target_ref="end_1")
    return BpmnDiagram(
        definitions_id="def_1",
        processes=[BpmnProcess(id="proc_1", flow_nodes=[task, end], sequence_flows=[flow])],
    )


def _duplicate_id_diagram() -> BpmnDiagram:
    diagram = _minimal_valid_diagram()
    duplicate = FlowNode(id="task_1", type=FlowNodeType.TASK)
    diagram.processes[0].flow_nodes.append(duplicate)
    return diagram
