import pytest
from pydantic import ValidationError

from app.model.schema import BpmnDiagram, BpmnProcess, FlowNode, FlowNodeType, SequenceFlow
from app.repair.ops import (
    AddNodeOp,
    ChangeGatewayTypeOp,
    ReplaceDiagramOp,
    RemoveNodeOp,
    SetConditionOp,
    apply_edit_ops,
    atomic_edit_op_list_adapter,
    edit_op_adapter,
    edit_op_list_adapter,
)


def test_edit_op_adapter_validates_add_node():
    op = edit_op_adapter.validate_python(
        {
            "op": "add_node",
            "id": "task_2",
            "node_type": "userTask",
            "process_id": "process_1",
            "name": "Review request",
        }
    )

    assert isinstance(op, AddNodeOp)
    assert op.node_type == FlowNodeType.USER_TASK


def test_remove_node_defaults_to_no_cascade():
    op = edit_op_adapter.validate_python({"op": "remove_node", "id": "task_1"})

    assert isinstance(op, RemoveNodeOp)
    assert op.cascade is False


def test_set_condition_accepts_null_to_clear_condition():
    op = edit_op_adapter.validate_python(
        {
            "op": "set_condition",
            "flow_id": "flow_1",
            "condition_expression": None,
        }
    )

    assert isinstance(op, SetConditionOp)
    assert op.condition_expression is None


def test_change_gateway_type_requires_gateway_type():
    op = edit_op_adapter.validate_python(
        {
            "op": "change_gateway_type",
            "id": "gateway_1",
            "new_type": "parallelGateway",
        }
    )

    assert isinstance(op, ChangeGatewayTypeOp)
    assert op.new_type == FlowNodeType.PARALLEL_GATEWAY

    with pytest.raises(ValidationError):
        edit_op_adapter.validate_python(
            {
                "op": "change_gateway_type",
                "id": "gateway_1",
                "new_type": "task",
            }
        )


def test_edit_op_list_adapter_rejects_unknown_operation():
    with pytest.raises(ValidationError):
        edit_op_list_adapter.validate_python([{"op": "replace_everything"}])


def test_atomic_adapter_excludes_replace_diagram_but_full_adapter_accepts_it():
    replacement = BpmnDiagram(
        definitions_id="def_replacement",
        processes=[BpmnProcess(id="replacement_proc")],
    )
    payload = [{"op": "replace_diagram", "diagram": replacement.model_dump()}]

    with pytest.raises(ValidationError):
        atomic_edit_op_list_adapter.validate_python(payload)

    ops = edit_op_list_adapter.validate_python(payload)

    assert isinstance(ops[0], ReplaceDiagramOp)


def test_apply_edit_ops_adds_node_and_flow_without_mutating_original():
    diagram = _minimal_valid_diagram()
    ops = edit_op_list_adapter.validate_python(
        [
            {
                "op": "add_node",
                "id": "task_2",
                "node_type": "userTask",
                "process_id": "proc_1",
                "name": "Review request",
            },
            {
                "op": "add_flow",
                "id": "sf_3",
                "source_ref": "task_1",
                "target_ref": "task_2",
            },
        ]
    )

    updated, results = apply_edit_ops(ops, diagram)
    proc = updated.processes[0]
    task_1 = _node(proc, "task_1")
    task_2 = _node(proc, "task_2")

    assert [result.applied for result in results] == [True, True]
    assert task_1.outgoing == ["sf_2", "sf_3"]
    assert task_2.incoming == ["sf_3"]
    assert "task_2" not in {node.id for node in diagram.processes[0].flow_nodes}


def test_apply_edit_ops_rolls_back_failed_op_and_continues():
    diagram = _minimal_valid_diagram()
    ops = edit_op_list_adapter.validate_python(
        [
            {
                "op": "add_node",
                "id": "task_1",
                "node_type": "task",
                "process_id": "proc_1",
            },
            {"op": "rename_element", "id": "task_1", "new_name": "Renamed task"},
        ]
    )

    updated, results = apply_edit_ops(ops, diagram)
    proc = updated.processes[0]

    assert results[0].applied is False
    assert "already exists" in (results[0].error or "")
    assert results[1].applied is True
    assert len([node for node in proc.flow_nodes if node.id == "task_1"]) == 1
    assert _node(proc, "task_1").name == "Renamed task"


def test_apply_edit_ops_remove_node_requires_cascade():
    diagram = _minimal_valid_diagram()
    ops = edit_op_list_adapter.validate_python(
        [{"op": "remove_node", "id": "task_1"}]
    )

    updated, results = apply_edit_ops(ops, diagram)

    assert results[0].applied is False
    assert "incident flows" in (results[0].error or "")
    assert _node(updated.processes[0], "task_1").id == "task_1"


def test_apply_edit_ops_remove_node_with_cascade_removes_incident_flows():
    diagram = _minimal_valid_diagram()
    ops = edit_op_list_adapter.validate_python(
        [{"op": "remove_node", "id": "task_1", "cascade": True}]
    )

    updated, results = apply_edit_ops(ops, diagram)
    proc = updated.processes[0]

    assert results[0].applied is True
    assert "task_1" not in {node.id for node in proc.flow_nodes}
    assert proc.sequence_flows == []
    assert _node(proc, "start_1").outgoing == []
    assert _node(proc, "end_1").incoming == []


def test_apply_edit_ops_removes_flow_and_updates_node_refs():
    diagram = _minimal_valid_diagram()
    ops = edit_op_list_adapter.validate_python(
        [{"op": "remove_flow", "id": "sf_1"}]
    )

    updated, results = apply_edit_ops(ops, diagram)
    proc = updated.processes[0]

    assert results[0].applied is True
    assert "sf_1" not in {flow.id for flow in proc.sequence_flows}
    assert _node(proc, "start_1").outgoing == []
    assert _node(proc, "task_1").incoming == []


def test_apply_edit_ops_remove_flow_rejects_missing_flow():
    diagram = _minimal_valid_diagram()
    ops = edit_op_list_adapter.validate_python(
        [{"op": "remove_flow", "id": "missing_flow"}]
    )

    updated, results = apply_edit_ops(ops, diagram)

    assert results[0].applied is False
    assert "does not exist" in (results[0].error or "")
    assert [flow.id for flow in updated.processes[0].sequence_flows] == ["sf_1", "sf_2"]


def test_apply_edit_ops_renames_sequence_flow():
    diagram = _minimal_valid_diagram()
    ops = edit_op_list_adapter.validate_python(
        [{"op": "rename_element", "id": "sf_1", "new_name": "handoff"}]
    )

    updated, results = apply_edit_ops(ops, diagram)

    assert results[0].applied is True
    assert updated.processes[0].sequence_flows[0].name == "handoff"


def test_apply_edit_ops_changes_node_type():
    diagram = _minimal_valid_diagram()
    ops = edit_op_list_adapter.validate_python(
        [{"op": "change_node_type", "id": "task_1", "new_type": "userTask"}]
    )

    updated, results = apply_edit_ops(ops, diagram)

    assert results[0].applied is True
    assert _node(updated.processes[0], "task_1").type == FlowNodeType.USER_TASK


def test_apply_edit_ops_change_gateway_type_rejects_non_gateway_node():
    diagram = _minimal_valid_diagram()
    ops = edit_op_list_adapter.validate_python(
        [
            {
                "op": "change_gateway_type",
                "id": "task_1",
                "new_type": "parallelGateway",
            }
        ]
    )

    updated, results = apply_edit_ops(ops, diagram)

    assert results[0].applied is False
    assert "not a gateway" in (results[0].error or "")
    assert _node(updated.processes[0], "task_1").type == FlowNodeType.TASK


def test_apply_edit_ops_changes_gateway_type():
    diagram = _gateway_diagram()
    ops = edit_op_list_adapter.validate_python(
        [
            {
                "op": "change_gateway_type",
                "id": "gateway_1",
                "new_type": "parallelGateway",
            }
        ]
    )

    updated, results = apply_edit_ops(ops, diagram)

    assert results[0].applied is True
    assert _node(updated.processes[0], "gateway_1").type == FlowNodeType.PARALLEL_GATEWAY


def test_apply_edit_ops_set_condition_updates_flow():
    diagram = _minimal_valid_diagram()
    ops = edit_op_list_adapter.validate_python(
        [
            {
                "op": "set_condition",
                "flow_id": "sf_2",
                "condition_expression": "approved == true",
            }
        ]
    )

    updated, results = apply_edit_ops(ops, diagram)

    assert results[0].applied is True
    assert updated.processes[0].sequence_flows[1].condition_expression == "approved == true"


def test_apply_edit_ops_set_condition_clears_existing_condition():
    diagram = _minimal_valid_diagram()
    diagram.processes[0].sequence_flows[1].condition_expression = "approved == true"
    ops = edit_op_list_adapter.validate_python(
        [{"op": "set_condition", "flow_id": "sf_2", "condition_expression": None}]
    )

    updated, results = apply_edit_ops(ops, diagram)

    assert results[0].applied is True
    assert updated.processes[0].sequence_flows[1].condition_expression is None


def test_apply_edit_ops_replaces_diagram():
    diagram = _minimal_valid_diagram()
    replacement = BpmnDiagram(
        definitions_id="def_replacement",
        processes=[BpmnProcess(id="replacement_proc")],
    )
    ops = [ReplaceDiagramOp(diagram=replacement)]

    updated, results = apply_edit_ops(ops, diagram)

    assert results[0].applied is True
    assert updated.definitions_id == "def_replacement"
    assert [proc.id for proc in updated.processes] == ["replacement_proc"]
    assert diagram.definitions_id == "def_1"


def test_apply_edit_ops_add_flow_rejects_missing_target():
    diagram = _minimal_valid_diagram()
    ops = edit_op_list_adapter.validate_python(
        [
            {
                "op": "add_flow",
                "id": "sf_3",
                "source_ref": "task_1",
                "target_ref": "missing_node",
            }
        ]
    )

    updated, results = apply_edit_ops(ops, diagram)

    assert results[0].applied is False
    assert "does not exist" in (results[0].error or "")
    assert "sf_3" not in {flow.id for flow in updated.processes[0].sequence_flows}


def test_apply_edit_ops_add_flow_rejects_duplicate_id():
    diagram = _minimal_valid_diagram()
    ops = edit_op_list_adapter.validate_python(
        [
            {
                "op": "add_flow",
                "id": "sf_1",
                "source_ref": "task_1",
                "target_ref": "end_1",
            }
        ]
    )

    updated, results = apply_edit_ops(ops, diagram)

    assert results[0].applied is False
    assert "already exists" in (results[0].error or "")
    assert [flow.id for flow in updated.processes[0].sequence_flows] == ["sf_1", "sf_2"]


def test_apply_edit_ops_set_condition_rejects_missing_flow():
    diagram = _minimal_valid_diagram()
    ops = edit_op_list_adapter.validate_python(
        [
            {
                "op": "set_condition",
                "flow_id": "missing_flow",
                "condition_expression": "approved == true",
            }
        ]
    )

    updated, results = apply_edit_ops(ops, diagram)

    assert results[0].applied is False
    assert "does not exist" in (results[0].error or "")
    assert updated.processes[0].sequence_flows[1].condition_expression is None


def _minimal_valid_diagram() -> BpmnDiagram:
    start = FlowNode(id="start_1", type=FlowNodeType.START_EVENT, outgoing=["sf_1"])
    task = FlowNode(
        id="task_1",
        type=FlowNodeType.TASK,
        name="Do something",
        incoming=["sf_1"],
        outgoing=["sf_2"],
    )
    end = FlowNode(id="end_1", type=FlowNodeType.END_EVENT, incoming=["sf_2"])
    flows = [
        SequenceFlow(id="sf_1", source_ref="start_1", target_ref="task_1"),
        SequenceFlow(id="sf_2", source_ref="task_1", target_ref="end_1"),
    ]
    proc = BpmnProcess(id="proc_1", flow_nodes=[start, task, end], sequence_flows=flows)
    return BpmnDiagram(definitions_id="def_1", processes=[proc])


def _gateway_diagram() -> BpmnDiagram:
    diagram = _minimal_valid_diagram()
    proc = diagram.processes[0]
    proc.flow_nodes[1] = FlowNode(
        id="gateway_1",
        type=FlowNodeType.EXCLUSIVE_GATEWAY,
        incoming=["sf_1"],
        outgoing=["sf_2"],
    )
    proc.sequence_flows[0].target_ref = "gateway_1"
    proc.sequence_flows[1].source_ref = "gateway_1"
    return diagram


def _node(proc: BpmnProcess, node_id: str) -> FlowNode:
    return next(node for node in proc.flow_nodes if node.id == node_id)
