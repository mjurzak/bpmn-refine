from app.model.schema import BpmnDiagram, BpmnProcess, FlowNode, FlowNodeType, SequenceFlow
from app.repair.ops import apply_edit_ops
from app.repair.quick_fixes import propose_quick_fix
from app.validation.rules import validate


def test_quick_fix_adds_missing_start_event_and_entry_flow():
    diagram = _diagram_without_start()
    issue = validate(diagram).errors()[0]

    ops = propose_quick_fix(issue, diagram)
    updated, results = apply_edit_ops(ops or [], diagram)

    assert [op.op for op in ops or []] == ["add_node", "add_flow"]
    assert all(result.applied for result in results)
    assert validate(updated).is_valid is True


def test_quick_fix_removes_sequence_flow_with_unknown_target():
    diagram = _diagram_with_dangling_flow()
    issue = next(item for item in validate(diagram).errors() if item.rule_id == "R010")

    ops = propose_quick_fix(issue, diagram)
    updated, results = apply_edit_ops(ops or [], diagram)

    assert [op.op for op in ops or []] == ["remove_flow"]
    assert all(result.applied for result in results)
    assert validate(updated).is_valid is True
    assert {flow.id for flow in updated.processes[0].sequence_flows} == {"sf_1"}


def test_quick_fix_bypasses_single_branch_gateway():
    diagram = _single_branch_gateway_diagram()
    issue = next(item for item in validate(diagram).warnings() if item.rule_id == "R007")

    ops = propose_quick_fix(issue, diagram)
    updated, results = apply_edit_ops(ops or [], diagram)
    proc = updated.processes[0]

    assert [op.op for op in ops or []] == ["add_flow", "remove_node"]
    assert all(result.applied for result in results)
    assert "gateway_1" not in {node.id for node in proc.flow_nodes}
    assert len(proc.sequence_flows) == 1
    assert proc.sequence_flows[0].source_ref == "start_1"
    assert proc.sequence_flows[0].target_ref == "end_1"
    assert validate(updated).is_valid is True


def _diagram_without_start() -> BpmnDiagram:
    task = FlowNode(id="task_1", type=FlowNodeType.TASK, outgoing=["sf_1"])
    end = FlowNode(id="end_1", type=FlowNodeType.END_EVENT, incoming=["sf_1"])
    flow = SequenceFlow(id="sf_1", source_ref="task_1", target_ref="end_1")
    return BpmnDiagram(
        definitions_id="def_1",
        processes=[BpmnProcess(id="proc_1", flow_nodes=[task, end], sequence_flows=[flow])],
    )


def _diagram_with_dangling_flow() -> BpmnDiagram:
    start = FlowNode(id="start_1", type=FlowNodeType.START_EVENT, outgoing=["sf_1", "sf_bad"])
    end = FlowNode(id="end_1", type=FlowNodeType.END_EVENT, incoming=["sf_1"])
    flows = [
        SequenceFlow(id="sf_1", source_ref="start_1", target_ref="end_1"),
        SequenceFlow(id="sf_bad", source_ref="start_1", target_ref="missing_node"),
    ]
    return BpmnDiagram(
        definitions_id="def_1",
        processes=[BpmnProcess(id="proc_1", flow_nodes=[start, end], sequence_flows=flows)],
    )


def _single_branch_gateway_diagram() -> BpmnDiagram:
    start = FlowNode(id="start_1", type=FlowNodeType.START_EVENT, outgoing=["sf_1"])
    gateway = FlowNode(
        id="gateway_1",
        type=FlowNodeType.EXCLUSIVE_GATEWAY,
        incoming=["sf_1"],
        outgoing=["sf_2"],
    )
    end = FlowNode(id="end_1", type=FlowNodeType.END_EVENT, incoming=["sf_2"])
    flows = [
        SequenceFlow(id="sf_1", source_ref="start_1", target_ref="gateway_1"),
        SequenceFlow(id="sf_2", source_ref="gateway_1", target_ref="end_1"),
    ]
    return BpmnDiagram(
        definitions_id="def_1",
        processes=[BpmnProcess(id="proc_1", flow_nodes=[start, gateway, end], sequence_flows=flows)],
    )
