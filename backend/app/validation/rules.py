"""Deterministic rule-based BPMN validation (tier 1).

These rules run before any LLM call and without external dependencies. Each rule
falls into one of two justification classes (see `docs/validation-rules.md`):

* **A — integrity / translation preconditions** (R001-R006). Tier 1 is what makes tier 2 runnable.
* **B — live under-approximations of soundness** (R007-R008). Linear-time,
  element-local reachability checks where *firing implies the model is
  necessarily unsound*. Tier 2 would catch them too, but only via a global
  state-space walk that cannot run on every edit.

Every tier-1 rule is an `error`: firing guarantees a real defect. Heuristic,
"might be a problem" checks (disconnected fragments, gateway split/join
mismatches, ambiguous or no-op gateways) are deliberately left to the formal
checker (tier 2) and the LLM review (tier 3) rather than producing deterministic
warnings here.

Implicit splits are the exception: they fall to tier 3 *alone*. Tier 2 cannot
take that delegation, structurally — pm4py encodes a node with several outgoing
flows as a **free choice** in the Petri net (one exit place feeding several
transitions) where BPMN 2.0 specifies **parallel**, so Woflan reports SOUND on a
model that ends in contradictory states. Verified 2026-07-21; recorded as a named
tier-2 limitation in TODO.md under 2f. Promoting the check to tier 1 is not the
fix either: an uncontrolled split is legal BPMN, so the rule could not guarantee
a defect, and that invariant is worth more than the heuristic.

Multiple start events are deliberately not checked: BPMN 2.0 permits them and a
sound model can have many, so flagging them only produced noise.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.model.schema import (
    BpmnDiagram,
    BpmnProcess,
    FlowNode,
    FlowNodeId,
    FlowNodeType,
)

RULES_VERSION = "R001-R008"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationTier(StrEnum):
    TIER1 = "tier1"
    TIER2 = "tier2"
    TIER3 = "tier3"


class SemanticCategory(StrEnum):
    """The kind of semantic defect a tier-3 finding reports."""

    MISSING_STEP = "missing_step"
    CONTRADICTORY_FLOW = "contradictory_flow"
    UNREACHABLE_BRANCH = "unreachable_branch"
    MISSING_EXCEPTION_HANDLING = "missing_exception_handling"
    INCONSISTENT_NAMING = "inconsistent_naming"
    IMPROPER_TERMINATION = "improper_termination"


def semantic_rule_id(category: SemanticCategory) -> str:
    """the stable issue identifier for a semantic category"""
    return f"semantic:{category.value}"


@dataclass
class TraceStep:
    step: int
    fired: str
    marking_after: dict[str, int] = field(default_factory=dict)


@dataclass
class FormalWitness:
    kind: str
    description: str
    trace: list[TraceStep] = field(default_factory=list)
    counterexample_traces: list[list[TraceStep]] = field(default_factory=list)
    marking: dict[str, int] = field(default_factory=dict)
    dead_elements: list[str] = field(default_factory=list)
    uncovered_elements: list[str] = field(default_factory=list)


@dataclass
class ValidationIssue:
    rule_id: str
    severity: Severity
    message: str
    tier: ValidationTier | None = None
    element_id: str | None = None
    suggestion: str | None = None
    element_refs: list[str] = field(default_factory=list)
    source: str | None = None
    formal_witness: FormalWitness | None = None
    raw: dict[str, Any] | None = None


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


# issue.source values that are not a tier-2 tool name. tier 2 stamps its own tool
# (e.g. "woflan"), which is deterministic like the rules and more informative
SOURCE_RULES = "rules"
SOURCE_LLM = "llm"


def issue_to_dict(
    issue: ValidationIssue, include_formal_evidence: bool = True
) -> dict[str, Any]:
    """convert an issue to the compact, BPMN-level form used in LLM prompts

    `include_formal_evidence` is the counterexample ablation switch. Off, the
    issue still reaches the model with the same verdict and affected elements,
    but without the traces and markings that localise it — which isolates the
    evidence's contribution from the check's.
    """
    affected_elements: list[str] = []
    if issue.element_id:
        affected_elements.append(issue.element_id)
    for element_ref in issue.element_refs:
        if element_ref not in affected_elements:
            affected_elements.append(element_ref)

    data: dict[str, Any] = {
        "rule_id": issue.rule_id,
        "severity": str(issue.severity),
        "message": issue.message,
    }
    if issue.tier:
        data["tier"] = str(issue.tier)
    if issue.source:
        data["source"] = issue.source
    if affected_elements:
        data["affected_elements"] = affected_elements
    if issue.formal_witness and include_formal_evidence:
        witness = issue.formal_witness
        evidence: dict[str, Any] = {"kind": witness.kind}
        if witness.dead_elements:
            evidence["dead_elements"] = witness.dead_elements
        if witness.uncovered_elements:
            evidence["uncovered_elements"] = witness.uncovered_elements
        traces = witness.counterexample_traces or (
            [witness.trace] if witness.trace else []
        )
        if traces:
            evidence["counterexample_traces"] = [
                [step.fired for step in trace] for trace in traces
            ]
        if witness.marking:
            evidence["marking"] = witness.marking
        data["formal_evidence"] = evidence
    return data


_START_EVENT_TYPES = {FlowNodeType.START_EVENT}
_END_EVENT_TYPES = {FlowNodeType.END_EVENT}


class _Graph:
    """Adjacency view of one process, derived from its sequence flows."""

    def __init__(self, proc: BpmnProcess) -> None:
        self.proc = proc
        self.nodes: dict[FlowNodeId, FlowNode] = {n.id: n for n in proc.flow_nodes}
        self.succ: dict[FlowNodeId, list[FlowNodeId]] = {
            n.id: [] for n in proc.flow_nodes
        }
        self.pred: dict[FlowNodeId, list[FlowNodeId]] = {
            n.id: [] for n in proc.flow_nodes
        }
        for sf in proc.sequence_flows:
            # only wire edges whose endpoints both exist; dangling refs are R005/R006
            if sf.source_ref in self.nodes and sf.target_ref in self.nodes:
                self.succ[sf.source_ref].append(sf.target_ref)
                self.pred[sf.target_ref].append(sf.source_ref)

    def is_connected(self, node: FlowNode) -> bool:
        return bool(self.succ[node.id] or self.pred[node.id])

    def _reachable(
        self, seeds: list[FlowNodeId], edges: dict[FlowNodeId, list[FlowNodeId]]
    ) -> set[FlowNodeId]:
        seen: set[FlowNodeId] = set(seeds)
        queue = deque(seeds)
        while queue:
            current = queue.popleft()
            for nxt in edges.get(current, []):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    def reachable_from_starts(self) -> set[FlowNodeId]:
        seeds = [n.id for n in self.proc.flow_nodes if n.type in _START_EVENT_TYPES]
        return self._reachable(seeds, self.succ)

    def reaching_ends(self) -> set[FlowNodeId]:
        seeds = [n.id for n in self.proc.flow_nodes if n.type in _END_EVENT_TYPES]
        return self._reachable(seeds, self.pred)


def validate(diagram: BpmnDiagram) -> ValidationReport:
    """Run all deterministic rules and return a consolidated report."""
    report = ValidationReport()
    for proc in diagram.processes:
        graph = _Graph(proc)
        # class A — integrity / translation preconditions
        _check_start_events(proc, report)  # R001
        _check_end_events(proc, report)  # R002
        _check_event_connectivity(proc, report)  # R003, R004
        _check_sequence_flow_refs(proc, report)  # R005, R006
        # class B — live under-approximations of soundness
        _check_reachability(proc, graph, report)  # R007, R008
    # stamped centrally rather than at each construction site: consumers need to
    # tell a deterministic verdict from an LLM opinion, and after a repair every
    # issue arrives in one flat list with no other way to tell them apart
    for issue in report.issues:
        if issue.source is None:
            issue.source = SOURCE_RULES
        if issue.tier is None:
            issue.tier = ValidationTier.TIER1
    return report


# ---------------------------------------------------------------------------
# class A — integrity / translation preconditions
# ---------------------------------------------------------------------------


def _check_start_events(proc: BpmnProcess, report: ValidationReport) -> None:
    starts = [n for n in proc.flow_nodes if n.type in _START_EVENT_TYPES]
    if not starts:
        report.issues.append(
            ValidationIssue(
                rule_id="R001",
                severity=Severity.ERROR,
                message=f"Process '{proc.id}' has no start event.",
                suggestion="Add a start event as the entry point of the process.",
            )
        )


def _check_end_events(proc: BpmnProcess, report: ValidationReport) -> None:
    ends = [n for n in proc.flow_nodes if n.type in _END_EVENT_TYPES]
    if not ends:
        report.issues.append(
            ValidationIssue(
                rule_id="R002",
                severity=Severity.ERROR,
                message=f"Process '{proc.id}' has no end event.",
                suggestion="Add an end event to mark the process termination.",
            )
        )


def _check_event_connectivity(proc: BpmnProcess, report: ValidationReport) -> None:
    for node in proc.flow_nodes:
        if node.type in _START_EVENT_TYPES and not node.outgoing:
            report.issues.append(
                ValidationIssue(
                    rule_id="R003",
                    severity=Severity.ERROR,
                    message=f"Start event '{node.id}' has no outgoing sequence flow.",
                    element_id=node.id,
                    suggestion=(
                        "Connect the start event to the activity that should follow "
                        "it, or remove the start event if nothing actually triggers "
                        "the process there."
                    ),
                )
            )
        elif node.type in _END_EVENT_TYPES and not node.incoming:
            report.issues.append(
                ValidationIssue(
                    rule_id="R004",
                    severity=Severity.ERROR,
                    message=f"End event '{node.id}' has no incoming sequence flow.",
                    element_id=node.id,
                    suggestion=(
                        "Route the activity that should terminate here to the end "
                        "event, or remove the end event if the outcome is already "
                        "covered elsewhere."
                    ),
                )
            )


def _check_sequence_flow_refs(proc: BpmnProcess, report: ValidationReport) -> None:
    node_ids = {n.id for n in proc.flow_nodes}
    for sf in proc.sequence_flows:
        if sf.source_ref not in node_ids:
            report.issues.append(
                ValidationIssue(
                    rule_id="R005",
                    severity=Severity.ERROR,
                    message=f"Sequence flow '{sf.id}' references unknown source '{sf.source_ref}'.",
                    element_id=sf.id,
                )
            )
        if sf.target_ref not in node_ids:
            report.issues.append(
                ValidationIssue(
                    rule_id="R006",
                    severity=Severity.ERROR,
                    message=f"Sequence flow '{sf.id}' references unknown target '{sf.target_ref}'.",
                    element_id=sf.id,
                )
            )


# ---------------------------------------------------------------------------
# class B — live under-approximations of soundness
# ---------------------------------------------------------------------------


def _check_reachability(
    proc: BpmnProcess, graph: _Graph, report: ValidationReport
) -> None:
    """R007 / R008 — exact, linear-time under-approximation of soundness.

    A *connected* node that cannot be reached from any start event can never be
    activated (a dead node); a connected node that cannot reach any end event is
    a trap from which the process can never properly complete. Either condition
    guarantees the model is unsound — so firing is never a false alarm. Fully
    disconnected nodes are skipped: in isolation they are not a soundness defect
    a deterministic rule should hard-fail on, so they are left to tier 2 / tier 3.

    Reachability is only meaningful once the process has an entry and an exit, so
    each direction is suppressed when its anchor is missing — otherwise the
    derived errors would just echo R001 / R002 across every node.
    """
    has_start = any(n.type in _START_EVENT_TYPES for n in proc.flow_nodes)
    has_end = any(n.type in _END_EVENT_TYPES for n in proc.flow_nodes)
    from_start = graph.reachable_from_starts() if has_start else None
    to_end = graph.reaching_ends() if has_end else None
    for node in proc.flow_nodes:
        if not graph.is_connected(node):
            continue  # isolated node — not a deterministic hard error
        if (
            from_start is not None
            and node.type not in _START_EVENT_TYPES
            and node.id not in from_start
        ):
            report.issues.append(
                ValidationIssue(
                    rule_id="R007",
                    severity=Severity.ERROR,
                    message=(
                        f"Element '{node.id}' is not reachable from any start event "
                        "and can never be activated."
                    ),
                    element_id=node.id,
                    suggestion="Connect it to the flow downstream of a start event, or remove it.",
                )
            )
        if (
            to_end is not None
            and node.type not in _END_EVENT_TYPES
            and node.id not in to_end
        ):
            report.issues.append(
                ValidationIssue(
                    rule_id="R008",
                    severity=Severity.ERROR,
                    message=(
                        f"Element '{node.id}' cannot reach any end event — the process "
                        "can never complete through it (dead end / trap)."
                    ),
                    element_id=node.id,
                    suggestion="Route it towards an end event, or remove the dead branch.",
                )
            )
