"""Smoke tests for the deterministic validation rules."""
import pytest

from app.model.schema import BpmnDiagram, BpmnProcess, FlowNode, FlowNodeType, SequenceFlow
from app.validation.rules import Severity, validate


def _minimal_valid_diagram() -> BpmnDiagram:
    """build the simplest valid process: start → task → end"""
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
    assert "R003" in rule_ids


def test_duplicate_id():
    diagram = _minimal_valid_diagram()
    proc = diagram.processes[0]
    # add a node with the same id as task_1
    dup = FlowNode(id="task_1", type=FlowNodeType.TASK, name="Duplicate")
    proc.flow_nodes.append(dup)
    report = validate(diagram)
    rule_ids = {i.rule_id for i in report.errors()}
    assert "R011" in rule_ids
