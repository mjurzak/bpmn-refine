from pathlib import Path

import pytest
from lxml import etree

from app.model.formats.pydantic_ir import PydanticConverter


def test_serialize_handles_default_namespace_from_parsed_bpmn():
    converter = PydanticConverter()
    xml_bytes = Path("data/import_cases/pmo_01.bpmn").read_bytes()

    diagram = converter.parse(xml_bytes)
    serialised = converter.serialize(diagram)
    root = etree.fromstring(serialised)

    assert root.nsmap[None] == "http://www.omg.org/spec/BPMN/20100524/MODEL"


def test_parse_rejects_duplicate_element_ids():
    converter = PydanticConverter()
    xml_bytes = Path("data/import_cases/duplicate_id.bpmn").read_bytes()

    with pytest.raises(ValueError, match="Duplicate element ID 'task_main'"):
        converter.parse(xml_bytes)


def test_author_layout_survives_a_round_trip():
    """a repair must not re-draw a diagram it did not touch

    Before layout lived on the IR, parse threw BPMNDI away and serialize
    regenerated it as a single left-to-right row, so every branch collapsed and
    a one-flow repair produced a 100% visual diff.
    """
    from pathlib import Path

    from lxml import etree

    source = Path("data/test_cases/03_expense_reimbursement.bpmn").read_bytes()
    converter = PydanticConverter()
    out = converter.serialize(converter.parse(source))

    def geometry(payload: bytes):
        tree = etree.fromstring(payload)
        shapes, edges = {}, {}
        for el in tree.iter():
            if not isinstance(el.tag, str):
                continue
            local = etree.QName(el.tag).localname
            ref = el.get("bpmnElement")
            if local == "BPMNShape":
                bounds = el.find("{http://www.omg.org/spec/DD/20100524/DC}Bounds")
                shapes[ref] = (bounds.get("x"), bounds.get("y"),
                               bounds.get("width"), bounds.get("height"))
            elif local == "BPMNEdge":
                edges[ref] = [
                    (wp.get("x"), wp.get("y"))
                    for wp in el.findall("{http://www.omg.org/spec/DD/20100524/DI}waypoint")
                ]
        return shapes, edges

    assert geometry(source) == geometry(out)


def test_layout_is_generated_only_when_absent():
    """new nodes get placed clear of the author's work, which stays put"""
    from pathlib import Path

    from app.model.schema import FlowNodeType
    from app.repair.ops import AddNodeOp, apply_edit_ops

    converter = PydanticConverter()
    diagram = converter.parse(
        Path("data/test_cases/03_expense_reimbursement.bpmn").read_bytes()
    )
    before = {
        node.id: node.bounds for node in diagram.processes[0].flow_nodes
    }

    updated, _ = apply_edit_ops(
        [
            AddNodeOp(
                id="gw_new",
                node_type=FlowNodeType.EXCLUSIVE_GATEWAY,
                process_id="Process_expense_reimbursement",
            )
        ],
        diagram,
    )
    reparsed = converter.parse(converter.serialize(updated))
    after = {node.id: node.bounds for node in reparsed.processes[0].flow_nodes}

    for node_id, bounds in before.items():
        assert after[node_id] == bounds, f"{node_id} moved"

    placed = after["gw_new"]
    assert placed is not None
    rightmost = max(b.x + b.width for b in before.values() if b)
    assert placed.x >= rightmost, "a new node must not land on existing shapes"


UNTYPED_CONDITION = b"""<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             id="defs" targetNamespace="http://bpmn.io/schema/bpmn">
  <process id="p" isExecutable="false">
    <startEvent id="s"><outgoing>f</outgoing></startEvent>
    <endEvent id="e"><incoming>f</incoming></endEvent>
    <sequenceFlow id="f" sourceRef="s" targetRef="e">
      <conditionExpression>approved</conditionExpression>
    </sequenceFlow>
  </process>
</definitions>"""


def _condition_types(payload: bytes) -> list[str | None]:
    qname = "{http://www.w3.org/2001/XMLSchema-instance}type"
    tree = etree.fromstring(payload)
    return [
        el.get(qname)
        for el in tree.iter("{http://www.omg.org/spec/BPMN/20100524/MODEL}conditionExpression")
    ]


def test_condition_expression_keeps_its_xsi_type():
    """BPMN 2.0 specifies the narrowing; dropping it on export loses fidelity

    Case 03 is authored with xsi:type. Until this was fixed, serialize(parse(x))
    silently returned the expressions untyped, and tools that only read a
    tFormalExpression ignored every branch condition in the exported file.
    """
    source = Path("data/test_cases/03_expense_reimbursement.bpmn").read_bytes()
    converter = PydanticConverter()

    types = _condition_types(converter.serialize(converter.parse(source)))

    assert types, "fixture must carry condition expressions"
    # the prefix has to match what this document binds to the BPMN namespace
    assert set(types) == {"bpmn:tFormalExpression"}


def test_untyped_condition_expression_is_still_accepted_and_then_typed():
    """absence on read stays legal — plenty of real files omit it"""
    converter = PydanticConverter()

    diagram = converter.parse(UNTYPED_CONDITION)
    flow = diagram.processes[0].sequence_flows[0]
    assert flow.condition_expression == "approved"

    # BPMN is the default namespace here, so the QName carries no prefix
    assert _condition_types(converter.serialize(diagram)) == ["tFormalExpression"]


def test_xsi_is_not_declared_when_no_condition_needs_it():
    source = Path("data/test_cases/01_gateway_issues.bpmn").read_bytes()
    converter = PydanticConverter()

    root = etree.fromstring(converter.serialize(converter.parse(source)))

    assert "http://www.w3.org/2001/XMLSchema-instance" not in root.nsmap.values()
