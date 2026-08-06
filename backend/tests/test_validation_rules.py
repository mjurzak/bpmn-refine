"""Smoke tests for the deterministic validation rules."""
import pytest

from app.model.schema import BpmnDiagram, BpmnProcess, FlowNode, FlowNodeType, SequenceFlow
from app.validation.rules import Severity, validate


def _minimal_valid_diagram() -> BpmnDiagram:
    """The simplest valid process: start -> task -> end."""
    start = FlowNode(id="start_1", type=FlowNodeType.START_EVENT, outgoing=["sf_1"])
    task = FlowNode(id="task_1", type=FlowNodeType.TASK, name="Do something", incoming=["sf_1"], outgoing=["sf_2"])
    end = FlowNode(id="end_1", type=FlowNodeType.END_EVENT, incoming=["sf_2"])
    flows = [
        SequenceFlow(id="sf_1", source_ref="start_1", target_ref="task_1"),
        SequenceFlow(id="sf_2", source_ref="task_1", target_ref="end_1"),
    ]
    proc = BpmnProcess(id="proc_1", flow_nodes=[start, task, end], sequence_flows=flows)
    return BpmnDiagram(definitions_id="def_1", processes=[proc])


def test_valid_diagram_passes():
    report = validate(_minimal_valid_diagram())
    assert report.is_valid
    assert not report.errors()


def test_missing_start_event():
    diagram = _minimal_valid_diagram()
    proc = diagram.processes[0]
    proc.flow_nodes = [n for n in proc.flow_nodes if n.type != FlowNodeType.START_EVENT]
    report = validate(diagram)
    assert not report.is_valid
    rule_ids = {i.rule_id for i in report.errors()}
    assert "R001" in rule_ids


def test_missing_end_event():
    diagram = _minimal_valid_diagram()
    proc = diagram.processes[0]
    proc.flow_nodes = [n for n in proc.flow_nodes if n.type != FlowNodeType.END_EVENT]
    report = validate(diagram)
    assert not report.is_valid
    rule_ids = {i.rule_id for i in report.errors()}
    assert "R002" in rule_ids


def test_multiple_start_events_not_flagged():
    """Multiple start events are sound and deliberately not checked."""
    diagram = _minimal_valid_diagram()
    proc = diagram.processes[0]
    proc.flow_nodes.append(
        FlowNode(id="start_2", type=FlowNodeType.START_EVENT, outgoing=["sf_b"])
    )
    proc.sequence_flows.append(
        SequenceFlow(id="sf_b", source_ref="start_2", target_ref="task_1")
    )
    proc.flow_nodes[1].incoming.append("sf_b")  # task_1 now has two inbound flows
    report = validate(diagram)
    assert report.is_valid
    assert not report.warnings()


def test_unreachable_node_flags_r007():
    """A connected node with no path from a start can never be activated."""
    diagram = _minimal_valid_diagram()
    proc = diagram.processes[0]
    proc.flow_nodes.append(
        FlowNode(id="island", type=FlowNodeType.TASK, outgoing=["sf_i"])
    )
    proc.sequence_flows.append(
        SequenceFlow(id="sf_i", source_ref="island", target_ref="end_1")
    )
    proc.flow_nodes[2].incoming.append("sf_i")  # end_1 also reachable from island
    rule_ids = {i.rule_id for i in validate(diagram).errors()}
    assert "R007" in rule_ids


def test_trap_node_flags_r008():
    """A reachable node that cannot reach any end is a trap."""
    diagram = _minimal_valid_diagram()
    proc = diagram.processes[0]
    # dead is reachable from task_1 but cannot reach end_1
    proc.flow_nodes.append(FlowNode(id="dead", type=FlowNodeType.TASK, incoming=["sf_d"]))
    proc.sequence_flows.append(
        SequenceFlow(id="sf_d", source_ref="task_1", target_ref="dead")
    )
    proc.flow_nodes[1].outgoing.append("sf_d")
    rule_ids = {i.rule_id for i in validate(diagram).errors()}
    assert "R008" in rule_ids


def test_every_deterministic_issue_is_stamped_as_rules():
    """The UI tells a rule verdict from an LLM opinion by issue.source alone."""
    from pathlib import Path

    from app.model.formats.pydantic_ir import PydanticConverter
    from app.validation.rules import SOURCE_RULES

    diagram = PydanticConverter().parse(
        Path("data/test_cases/03_expense_reimbursement.bpmn").read_bytes()
    )
    issues = validate(diagram).issues
    assert issues
    assert all(issue.source == SOURCE_RULES for issue in issues)
