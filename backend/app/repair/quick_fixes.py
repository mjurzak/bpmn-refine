"""Deterministic tier-1 quick fixes for repair dispatch."""

from __future__ import annotations

from collections.abc import Callable

from app.model.schema import BpmnDiagram, BpmnProcess, FlowNode, FlowNodeType, SequenceFlow
from app.repair.ops import AddFlowOp, AddNodeOp, EditOp, RemoveFlowOp, RemoveNodeOp
from app.validation.rules import ValidationIssue

QuickFix = Callable[[ValidationIssue, BpmnDiagram], list[EditOp] | None]


def propose_quick_fix(
    issue: ValidationIssue,
    diagram: BpmnDiagram,
) -> list[EditOp] | None:
    fix = _REGISTRY.get(issue.rule_id)
    if fix is None:
        return None
    return fix(issue, diagram)


def _fix_missing_start(issue: ValidationIssue, diagram: BpmnDiagram) -> list[EditOp] | None:
    proc = _process_for_issue(issue, diagram, lambda item: not _has_node_type(item, FlowNodeType.START_EVENT))
    if proc is None:
        return None

    start_id = _unique_id(diagram, f"start_{_safe_id(proc.id)}")
    ops: list[EditOp] = [
        AddNodeOp(id=start_id, node_type=FlowNodeType.START_EVENT, process_id=proc.id)
    ]
    target = _best_entry_target(proc)
    if target is not None:
        ops.append(
            AddFlowOp(
                id=_unique_id(diagram, f"flow_{start_id}_to_{target.id}"),
                source_ref=start_id,
                target_ref=target.id,
            )
        )
    return ops


def _fix_missing_end(issue: ValidationIssue, diagram: BpmnDiagram) -> list[EditOp] | None:
    proc = _process_for_issue(issue, diagram, lambda item: not _has_node_type(item, FlowNodeType.END_EVENT))
    if proc is None:
        return None

    end_id = _unique_id(diagram, f"end_{_safe_id(proc.id)}")
    ops: list[EditOp] = [
        AddNodeOp(id=end_id, node_type=FlowNodeType.END_EVENT, process_id=proc.id)
    ]
    source = _best_exit_source(proc)
    if source is not None:
        ops.append(
            AddFlowOp(
                id=_unique_id(diagram, f"flow_{source.id}_to_{end_id}"),
                source_ref=source.id,
                target_ref=end_id,
            )
        )
    return ops


def _fix_start_without_outgoing(issue: ValidationIssue, diagram: BpmnDiagram) -> list[EditOp] | None:
    found = _find_node(diagram, issue.element_id)
    if found is None:
        return None
    proc, start = found
    target = _best_entry_target(proc, exclude_id=start.id)
    if target is None:
        return None
    return [
        AddFlowOp(
            id=_unique_id(diagram, f"flow_{start.id}_to_{target.id}"),
            source_ref=start.id,
            target_ref=target.id,
        )
    ]


def _fix_end_without_incoming(issue: ValidationIssue, diagram: BpmnDiagram) -> list[EditOp] | None:
    found = _find_node(diagram, issue.element_id)
    if found is None:
        return None
    proc, end = found
    source = _best_exit_source(proc, exclude_id=end.id)
    if source is None:
        return None
    return [
        AddFlowOp(
            id=_unique_id(diagram, f"flow_{source.id}_to_{end.id}"),
            source_ref=source.id,
            target_ref=end.id,
        )
    ]


def _fix_dangling_flow(issue: ValidationIssue, diagram: BpmnDiagram) -> list[EditOp] | None:
    if not issue.element_id or _find_flow(diagram, issue.element_id) is None:
        return None
    return [RemoveFlowOp(id=issue.element_id)]


def _fix_single_branch_gateway(issue: ValidationIssue, diagram: BpmnDiagram) -> list[EditOp] | None:
    found = _find_node(diagram, issue.element_id)
    if found is None:
        return None
    proc, gateway = found
    if len(gateway.incoming) != 1 or len(gateway.outgoing) != 1:
        return None

    incoming = _flow_in_process(proc, gateway.incoming[0])
    outgoing = _flow_in_process(proc, gateway.outgoing[0])
    if incoming is None or outgoing is None:
        return None
    if incoming.source_ref == gateway.id or outgoing.target_ref == gateway.id:
        return None

    return [
        AddFlowOp(
            id=_unique_id(diagram, f"flow_{incoming.source_ref}_to_{outgoing.target_ref}"),
            source_ref=incoming.source_ref,
            target_ref=outgoing.target_ref,
        ),
        RemoveNodeOp(id=gateway.id, cascade=True),
    ]


def _process_for_issue(
    issue: ValidationIssue,
    diagram: BpmnDiagram,
    predicate: Callable[[BpmnProcess], bool],
) -> BpmnProcess | None:
    for proc in diagram.processes:
        if f"'{proc.id}'" in issue.message and predicate(proc):
            return proc
    return next((proc for proc in diagram.processes if predicate(proc)), None)


def _has_node_type(proc: BpmnProcess, node_type: FlowNodeType) -> bool:
    return any(node.type == node_type for node in proc.flow_nodes)


def _best_entry_target(proc: BpmnProcess, exclude_id: str | None = None) -> FlowNode | None:
    candidates = [node for node in proc.flow_nodes if node.id != exclude_id]
    preferred = [node for node in candidates if not node.incoming and node.type != FlowNodeType.START_EVENT]
    return next(iter(preferred or candidates), None)


def _best_exit_source(proc: BpmnProcess, exclude_id: str | None = None) -> FlowNode | None:
    candidates = [node for node in proc.flow_nodes if node.id != exclude_id]
    preferred = [node for node in candidates if not node.outgoing and node.type != FlowNodeType.END_EVENT]
    return next(iter(preferred or candidates), None)


def _find_node(
    diagram: BpmnDiagram,
    node_id: str | None,
) -> tuple[BpmnProcess, FlowNode] | None:
    if node_id is None:
        return None
    for proc in diagram.processes:
        for node in proc.flow_nodes:
            if node.id == node_id:
                return proc, node
    return None


def _find_flow(
    diagram: BpmnDiagram,
    flow_id: str,
) -> tuple[BpmnProcess, SequenceFlow] | None:
    for proc in diagram.processes:
        flow = _flow_in_process(proc, flow_id)
        if flow is not None:
            return proc, flow
    return None


def _flow_in_process(proc: BpmnProcess, flow_id: str) -> SequenceFlow | None:
    return next((flow for flow in proc.sequence_flows if flow.id == flow_id), None)


def _unique_id(diagram: BpmnDiagram, base: str) -> str:
    existing = _all_ids(diagram)
    candidate = base
    index = 2
    while candidate in existing:
        candidate = f"{base}_{index}"
        index += 1
    return candidate


def _all_ids(diagram: BpmnDiagram) -> set[str]:
    ids = {proc.id for proc in diagram.processes}
    for proc in diagram.processes:
        ids.update(node.id for node in proc.flow_nodes)
        ids.update(flow.id for flow in proc.sequence_flows)
    return ids


def _safe_id(value: str) -> str:
    cleaned = "".join(
        char if (char.isascii() and char.isalnum()) or char == "_" else "_"
        for char in value
    )
    return cleaned or "process"


_REGISTRY: dict[str, QuickFix] = {
    "R001": _fix_missing_start,
    "R003": _fix_missing_end,
    "R004": _fix_start_without_outgoing,
    "R005": _fix_end_without_incoming,
    "R007": _fix_single_branch_gateway,
    "R009": _fix_dangling_flow,
    "R010": _fix_dangling_flow,
}
