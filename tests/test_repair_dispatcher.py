from app.experiments import ExperimentConfig
from app.model.schema import BpmnDiagram, BpmnProcess, FlowNode, FlowNodeType, SequenceFlow
from app.services.repair import RepairResult, dispatch_repair
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


async def test_dispatch_repair_uses_llm_fallback_when_no_quick_fix_exists():
    calls = []

    async def fake_repair(diagram, issues, **kwargs):
        calls.append((diagram, issues, kwargs))
        return RepairResult(repaired_diagram=_minimal_valid_diagram())

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
        repair_fn=fake_repair,
    )

    assert result.iterations == 1
    assert result.converged is True
    assert result.remaining_issues == []
    assert result.applied_ops == []
    assert calls[0][1][0].rule_id == "R011"
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
        config=ExperimentConfig(max_repair_iters=2),
        repair_fn=fake_repair,
    )

    assert result.iterations == 2
    assert result.converged is False
    assert calls == ["R011", "R011"]
    assert {issue.rule_id for issue in result.remaining_issues} >= {"R011"}


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
