"""Atomic edit operation schemas for BPMN repair."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator

from app.model.schema import BpmnDiagram, BpmnProcess, FlowNode, FlowNodeType, SequenceFlow


class EditOpType(StrEnum):
    ADD_NODE = "add_node"
    REMOVE_NODE = "remove_node"
    ADD_FLOW = "add_flow"
    REMOVE_FLOW = "remove_flow"
    RENAME_ELEMENT = "rename_element"
    CHANGE_NODE_TYPE = "change_node_type"
    CHANGE_GATEWAY_TYPE = "change_gateway_type"
    SET_CONDITION = "set_condition"
    REPLACE_DIAGRAM = "replace_diagram"


GATEWAY_NODE_TYPES = {
    FlowNodeType.EXCLUSIVE_GATEWAY,
    FlowNodeType.INCLUSIVE_GATEWAY,
    FlowNodeType.PARALLEL_GATEWAY,
    FlowNodeType.EVENT_BASED_GATEWAY,
    FlowNodeType.COMPLEX_GATEWAY,
}


class AddNodeOp(BaseModel):
    op: Literal[EditOpType.ADD_NODE] = EditOpType.ADD_NODE
    id: str
    node_type: FlowNodeType
    process_id: str
    name: str | None = None


class RemoveNodeOp(BaseModel):
    op: Literal[EditOpType.REMOVE_NODE] = EditOpType.REMOVE_NODE
    id: str
    cascade: bool = False


class AddFlowOp(BaseModel):
    op: Literal[EditOpType.ADD_FLOW] = EditOpType.ADD_FLOW
    id: str
    source_ref: str
    target_ref: str
    name: str | None = None
    condition_expression: str | None = None


class RemoveFlowOp(BaseModel):
    op: Literal[EditOpType.REMOVE_FLOW] = EditOpType.REMOVE_FLOW
    id: str


class RenameElementOp(BaseModel):
    op: Literal[EditOpType.RENAME_ELEMENT] = EditOpType.RENAME_ELEMENT
    id: str
    new_name: str


class ChangeNodeTypeOp(BaseModel):
    op: Literal[EditOpType.CHANGE_NODE_TYPE] = EditOpType.CHANGE_NODE_TYPE
    id: str
    new_type: FlowNodeType


class ChangeGatewayTypeOp(BaseModel):
    op: Literal[EditOpType.CHANGE_GATEWAY_TYPE] = EditOpType.CHANGE_GATEWAY_TYPE
    id: str
    new_type: FlowNodeType

    @field_validator("new_type")
    @classmethod
    def _new_type_must_be_gateway(cls, value: FlowNodeType) -> FlowNodeType:
        if value not in GATEWAY_NODE_TYPES:
            raise ValueError("new_type must be a gateway type")
        return value


class SetConditionOp(BaseModel):
    op: Literal[EditOpType.SET_CONDITION] = EditOpType.SET_CONDITION
    flow_id: str
    condition_expression: str | None


class ReplaceDiagramOp(BaseModel):
    op: Literal[EditOpType.REPLACE_DIAGRAM] = EditOpType.REPLACE_DIAGRAM
    diagram: BpmnDiagram


AtomicEditOp = Annotated[
    AddNodeOp
    | RemoveNodeOp
    | AddFlowOp
    | RemoveFlowOp
    | RenameElementOp
    | ChangeNodeTypeOp
    | ChangeGatewayTypeOp
    | SetConditionOp,
    Field(discriminator="op"),
]

EditOp = Annotated[
    AtomicEditOp | ReplaceDiagramOp,
    Field(discriminator="op"),
]

atomic_edit_op_list_adapter = TypeAdapter(list[AtomicEditOp])
edit_op_adapter = TypeAdapter(EditOp)
edit_op_list_adapter = TypeAdapter(list[EditOp])


class EditOpResult(BaseModel):
    op: EditOp
    applied: bool
    error: str | None = None


class EditOpError(ValueError):
    pass


def apply_edit_ops(
    ops: list[EditOp],
    diagram: BpmnDiagram,
) -> tuple[BpmnDiagram, list[EditOpResult]]:
    updated = diagram.model_copy(deep=True)
    results: list[EditOpResult] = []

    for op in ops:
        before = updated.model_copy(deep=True)
        try:
            _apply_one(op, updated)
        except EditOpError as exc:
            updated = before
            results.append(EditOpResult(op=op, applied=False, error=str(exc)))
            continue
        results.append(EditOpResult(op=op, applied=True))

    return updated, results


def _apply_one(op: EditOp, diagram: BpmnDiagram) -> None:
    if isinstance(op, AddNodeOp):
        _apply_add_node(op, diagram)
    elif isinstance(op, RemoveNodeOp):
        _apply_remove_node(op, diagram)
    elif isinstance(op, AddFlowOp):
        _apply_add_flow(op, diagram)
    elif isinstance(op, RemoveFlowOp):
        _apply_remove_flow(op, diagram)
    elif isinstance(op, RenameElementOp):
        _apply_rename_element(op, diagram)
    elif isinstance(op, ChangeNodeTypeOp):
        _apply_change_node_type(op, diagram)
    elif isinstance(op, ChangeGatewayTypeOp):
        _apply_change_gateway_type(op, diagram)
    elif isinstance(op, SetConditionOp):
        _apply_set_condition(op, diagram)
    elif isinstance(op, ReplaceDiagramOp):
        _apply_replace_diagram(op, diagram)
    else:
        raise EditOpError(f"unsupported edit operation: {op}")


def _apply_add_node(op: AddNodeOp, diagram: BpmnDiagram) -> None:
    proc = _require_process(diagram, op.process_id)
    _require_unique_id(diagram, op.id)
    proc.flow_nodes.append(
        FlowNode(id=op.id, type=op.node_type, name=op.name)
    )


def _apply_remove_node(op: RemoveNodeOp, diagram: BpmnDiagram) -> None:
    proc, node = _require_node(diagram, op.id)
    incident_flow_ids = set(node.incoming + node.outgoing)
    if incident_flow_ids and not op.cascade:
        raise EditOpError(f"node '{op.id}' still has incident flows")

    if op.cascade:
        for flow_id in list(incident_flow_ids):
            _remove_flow_by_id(diagram, flow_id)

    proc.flow_nodes = [item for item in proc.flow_nodes if item.id != op.id]


def _apply_add_flow(op: AddFlowOp, diagram: BpmnDiagram) -> None:
    _require_unique_id(diagram, op.id)
    source_proc, source = _require_node(diagram, op.source_ref)
    target_proc, target = _require_node(diagram, op.target_ref)
    if source_proc.id != target_proc.id:
        raise EditOpError("source and target nodes must belong to the same process")

    source_proc.sequence_flows.append(
        SequenceFlow(
            id=op.id,
            source_ref=op.source_ref,
            target_ref=op.target_ref,
            name=op.name,
            condition_expression=op.condition_expression,
        )
    )
    if op.id not in source.outgoing:
        source.outgoing.append(op.id)
    if op.id not in target.incoming:
        target.incoming.append(op.id)


def _apply_remove_flow(op: RemoveFlowOp, diagram: BpmnDiagram) -> None:
    _remove_flow_by_id(diagram, op.id)


def _apply_rename_element(op: RenameElementOp, diagram: BpmnDiagram) -> None:
    found = _find_node(diagram, op.id)
    if found is not None:
        _, node = found
        node.name = op.new_name
        return

    found_flow = _find_flow(diagram, op.id)
    if found_flow is not None:
        _, flow = found_flow
        flow.name = op.new_name
        return

    raise EditOpError(f"element '{op.id}' does not exist")


def _apply_change_node_type(op: ChangeNodeTypeOp, diagram: BpmnDiagram) -> None:
    _, node = _require_node(diagram, op.id)
    node.type = op.new_type


def _apply_change_gateway_type(op: ChangeGatewayTypeOp, diagram: BpmnDiagram) -> None:
    _, node = _require_node(diagram, op.id)
    if node.type not in GATEWAY_NODE_TYPES:
        raise EditOpError(f"node '{op.id}' is not a gateway")
    node.type = op.new_type


def _apply_set_condition(op: SetConditionOp, diagram: BpmnDiagram) -> None:
    _, flow = _require_flow(diagram, op.flow_id)
    flow.condition_expression = op.condition_expression


def _apply_replace_diagram(op: ReplaceDiagramOp, diagram: BpmnDiagram) -> None:
    replacement = op.diagram.model_copy(deep=True)
    diagram.definitions_id = replacement.definitions_id
    diagram.target_namespace = replacement.target_namespace
    diagram.processes = replacement.processes
    diagram.namespaces = replacement.namespaces


def _remove_flow_by_id(diagram: BpmnDiagram, flow_id: str) -> None:
    proc, flow = _require_flow(diagram, flow_id)
    proc.sequence_flows = [item for item in proc.sequence_flows if item.id != flow_id]
    source = _find_node(diagram, flow.source_ref)
    target = _find_node(diagram, flow.target_ref)
    if source is not None:
        source[1].outgoing = [item for item in source[1].outgoing if item != flow_id]
    if target is not None:
        target[1].incoming = [item for item in target[1].incoming if item != flow_id]


def _require_process(diagram: BpmnDiagram, process_id: str) -> BpmnProcess:
    for proc in diagram.processes:
        if proc.id == process_id:
            return proc
    raise EditOpError(f"process '{process_id}' does not exist")


def _require_unique_id(diagram: BpmnDiagram, element_id: str) -> None:
    if _find_node(diagram, element_id) or _find_flow(diagram, element_id):
        raise EditOpError(f"element id '{element_id}' already exists")


def _require_node(diagram: BpmnDiagram, node_id: str) -> tuple[BpmnProcess, FlowNode]:
    found = _find_node(diagram, node_id)
    if found is None:
        raise EditOpError(f"node '{node_id}' does not exist")
    return found


def _find_node(diagram: BpmnDiagram, node_id: str) -> tuple[BpmnProcess, FlowNode] | None:
    for proc in diagram.processes:
        for node in proc.flow_nodes:
            if node.id == node_id:
                return proc, node
    return None


def _require_flow(diagram: BpmnDiagram, flow_id: str) -> tuple[BpmnProcess, SequenceFlow]:
    found = _find_flow(diagram, flow_id)
    if found is None:
        raise EditOpError(f"flow '{flow_id}' does not exist")
    return found


def _find_flow(
    diagram: BpmnDiagram,
    flow_id: str,
) -> tuple[BpmnProcess, SequenceFlow] | None:
    for proc in diagram.processes:
        for flow in proc.sequence_flows:
            if flow.id == flow_id:
                return proc, flow
    return None
