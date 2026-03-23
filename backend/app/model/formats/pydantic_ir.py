"""Default converter: BPMN XML ↔ Pydantic BpmnDiagram model.

Implements app.model.protocol.DiagramConverter.
Keeps the round-trip property: XML → BpmnDiagram → XML should produce
semantically equivalent BPMN (modulo whitespace and attribute ordering).
"""
from __future__ import annotations

from lxml import etree

from app.model.schema import (
    BpmnDiagram,
    BpmnProcess,
    FlowNode,
    FlowNodeType,
    SequenceFlow,
)

# standard BPMN 2.0 namespace
BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"

_FLOW_NODE_TAGS = {t.value for t in FlowNodeType}


class PydanticConverter:
    """Converts between raw BPMN XML and the Pydantic BpmnDiagram model."""

    def parse(self, xml_bytes: bytes) -> BpmnDiagram:
        """Parse BPMN XML bytes and return a BpmnDiagram."""
        root = etree.fromstring(xml_bytes)
        ns = _extract_namespaces(root)
        bpmn_ns = _resolve_bpmn_ns(root)

        processes: list[BpmnProcess] = []
        for proc_el in root.findall(f"{{{bpmn_ns}}}process"):
            processes.append(_parse_process(proc_el, bpmn_ns))

        return BpmnDiagram(
            definitions_id=root.get("id", "definitions"),
            target_namespace=root.get("targetNamespace", "http://bpmn.io/schema/bpmn"),
            processes=processes,
            namespaces=ns,
        )

    def serialize(self, diagram: BpmnDiagram) -> bytes:
        """Serialise a BpmnDiagram back to BPMN XML bytes."""
        nsmap: dict = diagram.namespaces or {"bpmn": BPMN_NS}
        root = etree.Element(
            f"{{{BPMN_NS}}}definitions",
            nsmap=nsmap,
            attrib={
                "id": diagram.definitions_id,
                "targetNamespace": diagram.target_namespace,
            },
        )
        for proc in diagram.processes:
            root.append(_serialize_process(proc, BPMN_NS))

        return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8")


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------

def _extract_namespaces(root: etree._Element) -> dict[str, str]:
    return {prefix or "": uri for prefix, uri in root.nsmap.items()}


def _resolve_bpmn_ns(root: etree._Element) -> str:
    for uri in root.nsmap.values():
        if "BPMN" in uri and "MODEL" in uri:
            return uri
    return BPMN_NS


def _parse_process(proc_el: etree._Element, bpmn_ns: str) -> BpmnProcess:
    flow_nodes: list[FlowNode] = []
    sequence_flows: list[SequenceFlow] = []

    for child in proc_el:
        local = etree.QName(child.tag).localname
        if local in _FLOW_NODE_TAGS:
            flow_nodes.append(_parse_flow_node(child, local))
        elif local == "sequenceFlow":
            sequence_flows.append(_parse_sequence_flow(child))

    # wire incoming/outgoing on nodes
    node_index = {n.id: n for n in flow_nodes}
    for sf in sequence_flows:
        if sf.source_ref in node_index:
            node_index[sf.source_ref].outgoing.append(sf.id)
        if sf.target_ref in node_index:
            node_index[sf.target_ref].incoming.append(sf.id)

    return BpmnProcess(
        id=proc_el.get("id", "process_1"),
        name=proc_el.get("name"),
        is_executable=proc_el.get("isExecutable", "false").lower() == "true",
        flow_nodes=flow_nodes,
        sequence_flows=sequence_flows,
    )


def _parse_flow_node(el: etree._Element, local_name: str) -> FlowNode:
    extra = {k: v for k, v in el.attrib.items() if k not in ("id", "name")}
    return FlowNode(
        id=el.get("id", ""),
        type=FlowNodeType(local_name),
        name=el.get("name"),
        extra=extra,
    )


def _parse_sequence_flow(el: etree._Element) -> SequenceFlow:
    cond_expr = None
    for child in el:
        if etree.QName(child.tag).localname == "conditionExpression":
            cond_expr = (child.text or "").strip()
    return SequenceFlow(
        id=el.get("id", ""),
        source_ref=el.get("sourceRef", ""),
        target_ref=el.get("targetRef", ""),
        name=el.get("name"),
        condition_expression=cond_expr,
    )


def _serialize_process(proc: BpmnProcess, bpmn_ns: str) -> etree._Element:
    attrib: dict = {"id": proc.id, "isExecutable": str(proc.is_executable).lower()}
    if proc.name:
        attrib["name"] = proc.name
    el = etree.Element(f"{{{bpmn_ns}}}process", attrib=attrib)

    for node in proc.flow_nodes:
        node_el = etree.SubElement(
            el,
            f"{{{bpmn_ns}}}{node.type.value}",
            attrib={"id": node.id, **({"name": node.name} if node.name else {}), **node.extra},
        )
        for out_id in node.outgoing:
            etree.SubElement(node_el, f"{{{bpmn_ns}}}outgoing").text = out_id
        for in_id in node.incoming:
            etree.SubElement(node_el, f"{{{bpmn_ns}}}incoming").text = in_id

    for sf in proc.sequence_flows:
        sf_attrib: dict = {
            "id": sf.id,
            "sourceRef": sf.source_ref,
            "targetRef": sf.target_ref,
        }
        if sf.name:
            sf_attrib["name"] = sf.name
        sf_el = etree.SubElement(el, f"{{{bpmn_ns}}}sequenceFlow", attrib=sf_attrib)
        if sf.condition_expression:
            cond_el = etree.SubElement(sf_el, f"{{{bpmn_ns}}}conditionExpression")
            cond_el.text = sf.condition_expression

    return el
