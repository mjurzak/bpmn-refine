"""Regression tests for the four canonical-model boundaries.

Each of these guards a way the diagram could previously be left in a state the
rest of the pipeline had no single interpretation of: contradictory adjacency,
an ID collision the per-op checks did not scope widely enough, a plan whose
later ops address elements earlier ops removed, and an import that dropped what
it could not represent without saying so.
"""

from __future__ import annotations

import pytest
from app.model.formats.pydantic_ir import PydanticConverter
from app.model.schema import (
    BpmnDiagram,
    BpmnProcess,
    FlowNode,
    FlowNodeType,
    Lane,
    Pool,
    SequenceFlow,
)
from app.repair.ops import AddFlowOp, AddNodeOp, apply_edit_ops
from app.services.diagrams import (
    describe_unsupported,
    parse_bpmn_bytes_with_diagnostics,
)
from app.services.repair import _validate_atomic_op_ids
from pydantic import ValidationError


def _diagram() -> BpmnDiagram:
    return BpmnDiagram(
        definitions_id="defs_1",
        processes=[
            BpmnProcess(
                id="Process_1",
                flow_nodes=[
                    FlowNode(id="start_1", type=FlowNodeType.START_EVENT),
                    FlowNode(id="task_1", type=FlowNodeType.TASK, name="Do it"),
                    FlowNode(id="end_1", type=FlowNodeType.END_EVENT),
                ],
                sequence_flows=[
                    SequenceFlow(id="sf_1", source_ref="start_1", target_ref="task_1"),
                    SequenceFlow(id="sf_2", source_ref="task_1", target_ref="end_1"),
                ],
            )
        ],
    )


# --------------------------------------------------------------------------
# 1. one graph interpretation for every input path
# --------------------------------------------------------------------------


def test_direct_json_payload_gets_its_adjacency_rebuilt():
    """a JSON diagram whose node lists contradict its flows is normalised

    This is the path that used to skip the XML parser entirely: Pydantic checked
    both representations for type but never against each other.
    """
    payload = {
        "definitions_id": "defs_1",
        "processes": [
            {
                "id": "Process_1",
                "flow_nodes": [
                    # claims an outgoing flow that no sequence flow backs
                    {"id": "task_a", "type": "task", "outgoing": ["ghost_flow"]},
                    {"id": "task_b", "type": "task"},
                ],
                # ...while the real flow out of task_a is not listed on it at all
                "sequence_flows": [
                    {"id": "sf_1", "source_ref": "task_a", "target_ref": "task_b"}
                ],
            }
        ],
    }

    diagram = BpmnDiagram.model_validate(payload)
    nodes = {node.id: node for node in diagram.processes[0].flow_nodes}

    assert nodes["task_a"].outgoing == ["sf_1"]
    assert nodes["task_b"].incoming == ["sf_1"]


def test_adjacency_rebuild_is_idempotent_on_a_consistent_diagram():
    """re-validating a parsed diagram must not reorder or duplicate its edges"""
    diagram = _diagram()
    revalidated = BpmnDiagram.model_validate(diagram.model_dump())
    assert revalidated == diagram


def test_dangling_flow_endpoint_wires_nothing_but_is_retained():
    """R005/R006 need the broken flow to survive so they can name it"""
    diagram = BpmnDiagram.model_validate(
        {
            "definitions_id": "defs_1",
            "processes": [
                {
                    "id": "Process_1",
                    "flow_nodes": [{"id": "task_a", "type": "task"}],
                    "sequence_flows": [
                        {"id": "sf_1", "source_ref": "task_a", "target_ref": "missing"}
                    ],
                }
            ],
        }
    )
    process = diagram.processes[0]
    assert process.flow_nodes[0].outgoing == ["sf_1"]
    assert len(process.sequence_flows) == 1


# --------------------------------------------------------------------------
# 2. the whole document is revalidated after an atomic edit
# --------------------------------------------------------------------------


def test_added_node_cannot_take_a_process_id():
    """`xsd:ID` is document-scoped, so a process name is a taken name"""
    diagram = _diagram()
    updated, results = apply_edit_ops(
        [
            AddNodeOp(
                id="Process_1", node_type=FlowNodeType.TASK, process_id="Process_1"
            )
        ],
        diagram,
    )

    assert results[0].applied is False
    assert "already exists" in (results[0].error or "")
    assert len(updated.processes[0].flow_nodes) == 3


def test_added_node_cannot_take_a_pool_or_lane_id():
    diagram = _diagram()
    diagram.processes[0].pools = [
        Pool(id="Pool_1", lanes=[Lane(id="Lane_1", name="Reviewers")])
    ]

    _, results = apply_edit_ops(
        [
            AddNodeOp(id="Lane_1", node_type=FlowNodeType.TASK, process_id="Process_1"),
            AddNodeOp(id="Pool_1", node_type=FlowNodeType.TASK, process_id="Process_1"),
        ],
        diagram,
    )

    assert [result.applied for result in results] == [False, False]


def test_applied_edit_leaves_the_diagram_revalidated():
    """the result of an apply is a model that passes construction-time checks"""
    diagram = _diagram()
    updated, results = apply_edit_ops(
        [
            AddNodeOp(
                id="task_2",
                node_type=FlowNodeType.TASK,
                process_id="Process_1",
                name="Second",
            ),
            AddFlowOp(
                process_id="Process_1",
                id="sf_3",
                source_ref="task_1",
                target_ref="task_2",
            ),
        ],
        diagram,
    )

    assert all(result.applied for result in results)
    # adjacency was rebuilt by the revalidation, not left to the op to maintain
    nodes = {node.id: node for node in updated.processes[0].flow_nodes}
    assert nodes["task_1"].outgoing == ["sf_2", "sf_3"]
    assert nodes["task_2"].incoming == ["sf_3"]
    assert BpmnDiagram.model_validate(updated.model_dump()) == updated


def test_replace_diagram_op_rejects_a_duplicate_id_payload():
    """a replacement that collides internally must not become the new state"""
    with pytest.raises(ValidationError):
        BpmnDiagram.model_validate(
            {
                "definitions_id": "defs_1",
                "processes": [
                    {
                        "id": "Process_1",
                        "flow_nodes": [
                            {"id": "dup", "type": "task"},
                            {"id": "dup", "type": "task"},
                        ],
                    }
                ],
            }
        )


# --------------------------------------------------------------------------
# 3. ordered edit-plan validation
# --------------------------------------------------------------------------


def test_new_flow_id_cannot_collide_with_a_process_id():
    with pytest.raises(ValueError, match="Process_1"):
        _validate_atomic_op_ids(
            [
                {
                    "op": "add_flow",
                    "process_id": "Process_1",
                    "id": "Process_1",
                    "source_ref": "start_1",
                    "target_ref": "end_1",
                }
            ],
            _diagram(),
        )


def test_new_flow_id_cannot_collide_with_an_existing_flow():
    with pytest.raises(ValueError, match="sf_1"):
        _validate_atomic_op_ids(
            [
                {
                    "op": "add_flow",
                    "process_id": "Process_1",
                    "id": "sf_1",
                    "source_ref": "start_1",
                    "target_ref": "end_1",
                }
            ],
            _diagram(),
        )


def test_cascade_removal_retires_the_flows_it_takes_with_it():
    """sf_1 and sf_2 are incident to task_1, so neither survives the cascade"""
    with pytest.raises(ValueError, match="sf_2"):
        _validate_atomic_op_ids(
            [
                {"op": "remove_node", "id": "task_1", "cascade": True},
                {"op": "set_condition", "flow_id": "sf_2", "condition_expression": "x"},
            ],
            _diagram(),
        )


def test_cascade_removal_leaves_untouched_flows_addressable():
    diagram = _diagram()
    diagram.processes[0].flow_nodes.append(
        FlowNode(id="task_2", type=FlowNodeType.TASK)
    )
    diagram.processes[0].sequence_flows.append(
        SequenceFlow(id="sf_3", source_ref="task_2", target_ref="end_1")
    )

    # sf_3 is not incident to task_1, so removing task_1 must not retire it
    _validate_atomic_op_ids(
        [
            {"op": "remove_node", "id": "task_1", "cascade": True},
            {"op": "set_condition", "flow_id": "sf_3", "condition_expression": "x"},
        ],
        BpmnDiagram.model_validate(diagram.model_dump()),
    )


def test_node_added_earlier_is_a_legal_endpoint_later():
    """the forward walk must still grow, not only shrink"""
    _validate_atomic_op_ids(
        [
            {
                "op": "add_node",
                "id": "task_new",
                "node_type": "task",
                "process_id": "Process_1",
            },
            {
                "op": "add_flow",
                "process_id": "Process_1",
                "id": "sf_new",
                "source_ref": "task_1",
                "target_ref": "task_new",
            },
        ],
        _diagram(),
    )


def test_add_flow_endpoints_must_belong_to_its_process():
    diagram = _diagram()
    diagram.processes.append(
        BpmnProcess(
            id="Process_2",
            flow_nodes=[FlowNode(id="task_other", type=FlowNodeType.TASK)],
        )
    )

    with pytest.raises(ValueError, match="belongs to process 'Process_2'"):
        _validate_atomic_op_ids(
            [
                {
                    "op": "add_flow",
                    "process_id": "Process_1",
                    "id": "sf_cross_process",
                    "source_ref": "task_1",
                    "target_ref": "task_other",
                }
            ],
            diagram,
        )


# --------------------------------------------------------------------------
# 4. unsupported XML children are reported, not dropped in silence
# --------------------------------------------------------------------------


_XML_WITH_UNSUPPORTED = b"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  id="defs_1" targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:collaboration id="Collaboration_1">
    <bpmn:participant id="Participant_1" processRef="Process_1" />
  </bpmn:collaboration>
  <bpmn:process id="Process_1" isExecutable="true">
    <bpmn:laneSet id="LaneSet_1">
      <bpmn:lane id="Lane_1" name="Reviewers" />
    </bpmn:laneSet>
    <bpmn:startEvent id="start_1" />
    <bpmn:task id="task_1" name="Do it" />
    <bpmn:endEvent id="end_1" />
    <bpmn:dataObjectReference id="DataObj_1" />
    <bpmn:textAnnotation id="Annotation_1" />
    <bpmn:sequenceFlow id="sf_1" sourceRef="start_1" targetRef="task_1" />
    <bpmn:sequenceFlow id="sf_2" sourceRef="task_1" targetRef="end_1" />
  </bpmn:process>
</bpmn:definitions>
"""


def test_import_reports_unsupported_process_children():
    _, unsupported = PydanticConverter().parse_with_diagnostics(_XML_WITH_UNSUPPORTED)
    tags = {element.tag for element in unsupported}
    assert {"laneSet", "dataObjectReference", "textAnnotation"} <= tags


def test_import_reports_unsupported_definitions_children():
    _, unsupported = PydanticConverter().parse_with_diagnostics(_XML_WITH_UNSUPPORTED)
    collaboration = next(
        element for element in unsupported if element.tag == "collaboration"
    )
    assert collaboration.scope == "definitions"
    assert collaboration.element_id == "Collaboration_1"


def test_unsupported_elements_carry_their_location():
    _, unsupported = PydanticConverter().parse_with_diagnostics(_XML_WITH_UNSUPPORTED)
    lane_set = next(element for element in unsupported if element.tag == "laneSet")
    assert lane_set.scope == "process"
    assert lane_set.parent_id == "Process_1"
    assert lane_set.element_id == "LaneSet_1"


def test_supported_document_reports_nothing():
    xml = PydanticConverter().serialize(_diagram())
    _, unsupported = parse_bpmn_bytes_with_diagnostics(xml)
    assert unsupported == []
    assert describe_unsupported(unsupported) is None


def test_diagnostic_names_every_dropped_element():
    _, unsupported = parse_bpmn_bytes_with_diagnostics(_XML_WITH_UNSUPPORTED)
    warning = describe_unsupported(unsupported)
    assert warning is not None
    assert "laneSet" in warning
    assert "collaboration" in warning


def test_the_supported_subset_still_imports_intact():
    """the diagnostic reports a loss; it must not cause one"""
    diagram, _ = parse_bpmn_bytes_with_diagnostics(_XML_WITH_UNSUPPORTED)
    process = diagram.processes[0]
    assert [node.id for node in process.flow_nodes] == ["start_1", "task_1", "end_1"]
    assert [flow.id for flow in process.sequence_flows] == ["sf_1", "sf_2"]
