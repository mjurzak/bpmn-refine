"""Deterministic tier-1 quick fixes for repair dispatch."""

from __future__ import annotations

from collections.abc import Callable

from app.model.schema import (
    BpmnDiagram,
    BpmnProcess,
    FlowNode,
    FlowNodeId,
    FlowNodeType,
    SequenceFlow,
    SequenceFlowId,
)
from app.repair.ops import AddFlowOp, AddNodeOp, EditOp, RemoveFlowOp
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


def _fix_missing_start(
    issue: ValidationIssue, diagram: BpmnDiagram
) -> list[EditOp] | None:
    proc = _process_for_issue(
        issue, diagram, lambda item: not _has_node_type(item, FlowNodeType.START_EVENT)
    )
    if proc is None:
        return None

    start_id = _unique_node_id(diagram, f"start_{_safe_id(proc.id)}")
    ops: list[EditOp] = [
        AddNodeOp(id=start_id, node_type=FlowNodeType.START_EVENT, process_id=proc.id)
    ]
    target = _best_entry_target(proc)
    if target is not None:
        ops.append(
            AddFlowOp(
                process_id=proc.id,
                id=_unique_flow_id(diagram, f"flow_{start_id}_to_{target.id}"),
                source_ref=start_id,
                target_ref=target.id,
            )
        )
    return ops


def _fix_missing_end(
    issue: ValidationIssue, diagram: BpmnDiagram
) -> list[EditOp] | None:
    proc = _process_for_issue(
        issue, diagram, lambda item: not _has_node_type(item, FlowNodeType.END_EVENT)
    )
    if proc is None:
        return None

    end_id = _unique_node_id(diagram, f"end_{_safe_id(proc.id)}")
    ops: list[EditOp] = [
        AddNodeOp(id=end_id, node_type=FlowNodeType.END_EVENT, process_id=proc.id)
    ]
    source = _best_exit_source(proc)
    if source is not None:
        ops.append(
            AddFlowOp(
                process_id=proc.id,
                id=_unique_flow_id(diagram, f"flow_{source.id}_to_{end_id}"),
                source_ref=source.id,
                target_ref=end_id,
            )
        )
    return ops


# R003 and R004 deliberately have no quick fix; see the note on _REGISTRY.


def _fix_dangling_flow(
    issue: ValidationIssue, diagram: BpmnDiagram
) -> list[EditOp] | None:
    if issue.element_id is None:
        return None

    found = _find_flow(diagram, issue.element_id)
    if found is None:
        return None

    _, flow = found
    return [RemoveFlowOp(id=flow.id)]


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


def _best_entry_target(proc: BpmnProcess) -> FlowNode | None:
    """where a newly added start event should point

    A node with no incoming flow is the likely head of the process, so it wins;
    otherwise any non-start node will do. Only R001 uses this, and R001 means the
    process has no start event at all, so there is no risk of picking one.
    """
    candidates = [
        node for node in proc.flow_nodes if node.type != FlowNodeType.START_EVENT
    ]
    preferred = [node for node in candidates if not node.incoming]
    return next(iter(preferred or candidates), None)


def _best_exit_source(proc: BpmnProcess) -> FlowNode | None:
    """the mirror of _best_entry_target, for a newly added end event (R002)"""
    candidates = [
        node for node in proc.flow_nodes if node.type != FlowNodeType.END_EVENT
    ]
    preferred = [node for node in candidates if not node.outgoing]
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


def _unique_node_id(diagram: BpmnDiagram, base: str) -> FlowNodeId:
    return FlowNodeId(_unique_id(diagram, base))


def _unique_flow_id(diagram: BpmnDiagram, base: str) -> SequenceFlowId:
    return SequenceFlowId(_unique_id(diagram, base))


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


# A quick fix must be right whenever it fires — an issue it handles never reaches
# the LLM. R003 and R004 are unregistered on purpose: wiring an orphan event in
# versus deleting it is a judgement no local heuristic can make. On case 09 the
# old heuristic joined an unconnected start straight to an unconnected end,
# producing a process that escalated every statement without looking at it.
_REGISTRY: dict[str, QuickFix] = {
    "R001": _fix_missing_start,            # no start event
    "R002": _fix_missing_end,              # no end event
    "R005": _fix_dangling_flow,            # sequence flow unknown source
    "R006": _fix_dangling_flow,            # sequence flow unknown target
}
