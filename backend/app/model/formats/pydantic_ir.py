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

# standard BPMN 2.0 namespaces
BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"
DI_NS = "http://www.omg.org/spec/DD/20100524/DI"

# dimensions for auto-layout
_EVENT_SIZE = 36
_TASK_SIZE = (100, 80)
_GATEWAY_SIZE = 50
_H_GAP = 50
_Y_CENTER = 200

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
        nsmap = _normalise_nsmap(diagram.namespaces)
        _ensure_namespace(nsmap, None, BPMN_NS)
        _ensure_namespace(nsmap, "bpmndi", BPMNDI_NS)
        _ensure_namespace(nsmap, "dc", DC_NS)
        _ensure_namespace(nsmap, "di", DI_NS)

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

        # bpmn-js requires BPMNDI to render — generate a simple left-to-right layout
        for proc in diagram.processes:
            root.append(_serialize_bpmndi(proc))

        return etree.tostring(
            root, pretty_print=True, xml_declaration=True, encoding="UTF-8"
        )


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _extract_namespaces(root: etree._Element) -> dict[str, str]:
    return {prefix or "": uri for prefix, uri in root.nsmap.items()}


def _normalise_nsmap(namespaces: dict[str, str] | None) -> dict[None | str, str]:
    nsmap: dict[None | str, str] = {}
    if not namespaces:
        return nsmap

    for prefix, uri in namespaces.items():
        if not uri:
            continue
        nsmap[None if prefix == "" else prefix] = uri

    return nsmap


def _ensure_namespace(
    nsmap: dict[None | str, str], prefix: None | str, uri: str
) -> None:
    if uri not in nsmap.values():
        nsmap[prefix] = uri


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
            attrib={
                "id": node.id,
                **({"name": node.name} if node.name else {}),
                **node.extra,
            },
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


def _serialize_bpmndi(proc: BpmnProcess) -> etree._Element:
    """Generate minimal BPMNDI for bpmn-js rendering.

    Lays out nodes left-to-right in sequence flow order so the diagram is
    at least readable.  This is not a full auto-layout — just enough for
    bpmn-js to initialise its canvas.
    """
    diagram_el = etree.Element(
        f"{{{BPMNDI_NS}}}BPMNDiagram",
        attrib={"id": f"BPMNDiagram_{proc.id}"},
    )
    plane_el = etree.SubElement(
        diagram_el,
        f"{{{BPMNDI_NS}}}BPMNPlane",
        attrib={"id": f"BPMNPlane_{proc.id}", "bpmnElement": proc.id},
    )

    # build a simple left-to-right ordering following sequence flows
    ordered_ids = _topo_order(proc)
    node_positions: dict[str, tuple[int, int, int, int]] = {}  # id → (x, y, w, h)

    x = 150
    for node_id in ordered_ids:
        node = next((n for n in proc.flow_nodes if n.id == node_id), None)
        if node is None:
            continue
        w, h = _node_dimensions(node)
        y = _Y_CENTER - h // 2
        node_positions[node_id] = (x, y, w, h)
        x += w + _H_GAP

    # shapes
    for node_id, (bx, by, bw, bh) in node_positions.items():
        shape_el = etree.SubElement(
            plane_el,
            f"{{{BPMNDI_NS}}}BPMNShape",
            attrib={"id": f"{node_id}_di", "bpmnElement": node_id},
        )
        etree.SubElement(
            shape_el,
            f"{{{DC_NS}}}Bounds",
            attrib={"x": str(bx), "y": str(by), "width": str(bw), "height": str(bh)},
        )

    # edges — simple straight line from source center-right to target center-left
    for sf in proc.sequence_flows:
        src = node_positions.get(sf.source_ref)
        tgt = node_positions.get(sf.target_ref)
        if not src or not tgt:
            continue
        edge_el = etree.SubElement(
            plane_el,
            f"{{{BPMNDI_NS}}}BPMNEdge",
            attrib={"id": f"{sf.id}_di", "bpmnElement": sf.id},
        )
        # waypoint: right edge of source → left edge of target
        sx, sy, sw, sh = src
        tx, ty, tw, th = tgt
        etree.SubElement(
            edge_el,
            f"{{{DI_NS}}}waypoint",
            attrib={"x": str(sx + sw), "y": str(sy + sh // 2)},
        )
        etree.SubElement(
            edge_el,
            f"{{{DI_NS}}}waypoint",
            attrib={"x": str(tx), "y": str(ty + th // 2)},
        )

    return diagram_el


def _node_dimensions(node: FlowNode) -> tuple[int, int]:
    """Return (width, height) for a given node type."""
    if node.type.value in (
        "startEvent",
        "endEvent",
        "intermediateCatchEvent",
        "intermediateThrowEvent",
    ):
        return (_EVENT_SIZE, _EVENT_SIZE)
    if "Gateway" in node.type.value or "gateway" in node.type.value:
        return (_GATEWAY_SIZE, _GATEWAY_SIZE)
    return _TASK_SIZE


def _topo_order(proc: BpmnProcess) -> list[str]:
    """Topological sort of flow nodes by sequence flows (simple BFS)."""
    node_ids = {n.id for n in proc.flow_nodes}
    incoming_count: dict[str, int] = {nid: 0 for nid in node_ids}
    adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}

    for sf in proc.sequence_flows:
        if sf.source_ref in node_ids and sf.target_ref in node_ids:
            adjacency[sf.source_ref].append(sf.target_ref)
            incoming_count[sf.target_ref] += 1

    # start from nodes with no incoming edges
    queue = [nid for nid, c in incoming_count.items() if c == 0]
    ordered: list[str] = []
    while queue:
        nid = queue.pop(0)
        ordered.append(nid)
        for successor in adjacency[nid]:
            incoming_count[successor] -= 1
            if incoming_count[successor] == 0:
                queue.append(successor)

    # append any nodes not reached (disconnected)
    for nid in node_ids:
        if nid not in ordered:
            ordered.append(nid)

    return ordered
