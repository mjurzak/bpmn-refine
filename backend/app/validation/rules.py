"""Deterministic rule-based BPMN validation.

These rules run before any LLM call.  They encode structural constraints that
are always verifiable without natural language understanding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.model.schema import BpmnDiagram, BpmnProcess, FlowNodeType

RULES_VERSION = "R001-R011"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationIssue:
    rule_id: str
    severity: Severity
    message: str
    element_id: str | None = None
    suggestion: str | None = None


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not any(i.severity == Severity.ERROR for i in self.issues)

    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]


_START_EVENT_TYPES = {FlowNodeType.START_EVENT}
_END_EVENT_TYPES = {FlowNodeType.END_EVENT}
_GATEWAY_TYPES = {
    FlowNodeType.EXCLUSIVE_GATEWAY,
    FlowNodeType.INCLUSIVE_GATEWAY,
    FlowNodeType.PARALLEL_GATEWAY,
    FlowNodeType.EVENT_BASED_GATEWAY,
    FlowNodeType.COMPLEX_GATEWAY,
}


def validate(diagram: BpmnDiagram) -> ValidationReport:
    """Run all deterministic rules and return a consolidated report."""
    report = ValidationReport()
    for proc in diagram.processes:
        _check_start_events(proc, report)
        _check_end_events(proc, report)
        _check_orphaned_nodes(proc, report)
        _check_gateway_branches(proc, report)
        _check_sequence_flow_refs(proc, report)
        _check_duplicate_ids(proc, report)
    return report


# ---------------------------------------------------------------------------
# individual rules
# ---------------------------------------------------------------------------

def _check_start_events(proc: BpmnProcess, report: ValidationReport) -> None:
    starts = [n for n in proc.flow_nodes if n.type in _START_EVENT_TYPES]
    if not starts:
        report.issues.append(ValidationIssue(
            rule_id="R001",
            severity=Severity.ERROR,
            message=f"Process '{proc.id}' has no start event.",
            suggestion="Add a start event as the entry point of the process.",
        ))
    if len(starts) > 1:
        for s in starts[1:]:
            report.issues.append(ValidationIssue(
                rule_id="R002",
                severity=Severity.WARNING,
                message=f"Process '{proc.id}' has more than one start event.",
                element_id=s.id,
                suggestion="Consider whether multiple start events are intentional (e.g. event sub-processes).",
            ))


def _check_end_events(proc: BpmnProcess, report: ValidationReport) -> None:
    ends = [n for n in proc.flow_nodes if n.type in _END_EVENT_TYPES]
    if not ends:
        report.issues.append(ValidationIssue(
            rule_id="R003",
            severity=Severity.ERROR,
            message=f"Process '{proc.id}' has no end event.",
            suggestion="Add an end event to mark the process termination.",
        ))


def _check_orphaned_nodes(proc: BpmnProcess, report: ValidationReport) -> None:
    for node in proc.flow_nodes:
        if node.type in _START_EVENT_TYPES:
            if not node.outgoing:
                report.issues.append(ValidationIssue(
                    rule_id="R004",
                    severity=Severity.ERROR,
                    message=f"Start event '{node.id}' has no outgoing sequence flow.",
                    element_id=node.id,
                ))
        elif node.type in _END_EVENT_TYPES:
            if not node.incoming:
                report.issues.append(ValidationIssue(
                    rule_id="R005",
                    severity=Severity.ERROR,
                    message=f"End event '{node.id}' has no incoming sequence flow.",
                    element_id=node.id,
                ))
        else:
            if not node.incoming and not node.outgoing:
                report.issues.append(ValidationIssue(
                    rule_id="R006",
                    severity=Severity.WARNING,
                    message=f"Element '{node.id}' ({node.type}) is disconnected (no incoming or outgoing flows).",
                    element_id=node.id,
                ))


def _check_gateway_branches(proc: BpmnProcess, report: ValidationReport) -> None:
    for node in proc.flow_nodes:
        if node.type not in _GATEWAY_TYPES:
            continue
        if len(node.outgoing) < 2:
            report.issues.append(ValidationIssue(
                rule_id="R007",
                severity=Severity.WARNING,
                message=f"Gateway '{node.id}' has fewer than 2 outgoing flows — may be unnecessary.",
                element_id=node.id,
            ))
        if node.type == FlowNodeType.EXCLUSIVE_GATEWAY and len(node.outgoing) > 1:
            sf_map = {sf.id: sf for sf in proc.sequence_flows}
            unconditioned = [
                sf_id for sf_id in node.outgoing
                if sf_map.get(sf_id) and not sf_map[sf_id].condition_expression
            ]
            # one default path without condition is acceptable
            if len(unconditioned) > 1:
                report.issues.append(ValidationIssue(
                    rule_id="R008",
                    severity=Severity.WARNING,
                    message=(
                        f"Exclusive gateway '{node.id}' has {len(unconditioned)} outgoing flows "
                        "without condition expressions."
                    ),
                    element_id=node.id,
                    suggestion="Add condition expressions to outgoing flows, keeping at most one default path.",
                ))


def _check_sequence_flow_refs(proc: BpmnProcess, report: ValidationReport) -> None:
    node_ids = {n.id for n in proc.flow_nodes}
    for sf in proc.sequence_flows:
        if sf.source_ref not in node_ids:
            report.issues.append(ValidationIssue(
                rule_id="R009",
                severity=Severity.ERROR,
                message=f"Sequence flow '{sf.id}' references unknown source '{sf.source_ref}'.",
                element_id=sf.id,
            ))
        if sf.target_ref not in node_ids:
            report.issues.append(ValidationIssue(
                rule_id="R010",
                severity=Severity.ERROR,
                message=f"Sequence flow '{sf.id}' references unknown target '{sf.target_ref}'.",
                element_id=sf.id,
            ))


def _check_duplicate_ids(proc: BpmnProcess, report: ValidationReport) -> None:
    seen: set[str] = set()
    for node in proc.flow_nodes:
        if node.id in seen:
            report.issues.append(ValidationIssue(
                rule_id="R011",
                severity=Severity.ERROR,
                message=f"Duplicate element ID '{node.id}' in process '{proc.id}'.",
                element_id=node.id,
            ))
        seen.add(node.id)
