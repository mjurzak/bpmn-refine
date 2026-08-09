"""Default converter: BPMN XML <-> Pydantic BpmnDiagram model.

Round-trip holds up to whitespace and attribute ordering.
"""

from __future__ import annotations

from typing import Any, cast

from lxml import etree

from app.model.protocol import BaseDiagramConverter, UnsupportedElement
from app.model.schema import (
    Bounds,
    BpmnDiagram,
    BpmnProcess,
    EventDefinition,
    EventDefinitionType,
    FlowNode,
    FlowNodeId,
    FlowNodeType,
    SequenceFlow,
    SequenceFlowId,
    Waypoint,
)

# standard BPMN 2.0 namespaces
BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"
DI_NS = "http://www.omg.org/spec/DD/20100524/DI"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

# dimensions for auto-layout
_EVENT_SIZE = 36
_TASK_SIZE = (100, 80)
_GATEWAY_SIZE = 50
_H_GAP = 50
_Y_CENTER = 200

_FLOW_NODE_TAGS = {t.value for t in FlowNodeType}
_EVENT_DEFINITION_TAGS = {t.value for t in EventDefinitionType}

# definitions-level children the converter handles, and metadata it may skip
# because it carries no control flow
_HANDLED_DEFINITIONS_TAGS = {"process", "BPMNDiagram"}
_IGNORED_DEFINITIONS_TAGS = {"import", "extension", "relationship", "documentation"}


class PydanticConverter(BaseDiagramConverter):
    """Converts between raw BPMN XML and the Pydantic BpmnDiagram model."""

    def parse(self, payload: bytes) -> BpmnDiagram:
        """Parse BPMN XML bytes and return a BpmnDiagram."""
        diagram, _ = self.parse_with_diagnostics(payload)
        return diagram

    def parse_with_diagnostics(
        self, payload: bytes
    ) -> tuple[BpmnDiagram, list[UnsupportedElement]]:
        """Parse BPMN XML, also reporting the children the IR does not represent.

        The IR covers process-level control flow, so collaborations, lane sets,
        data objects, artifacts and extensions are dropped — but never silently.
        """
        root = etree.fromstring(payload)
        ns = _extract_namespaces(root)
        bpmn_ns = _resolve_bpmn_ns(root)
        unsupported: list[UnsupportedElement] = []

        processes: list[BpmnProcess] = []
        for proc_el in root.findall(f"{{{bpmn_ns}}}process"):
            processes.append(_parse_process(proc_el, unsupported))

        _collect_unsupported_definitions_children(root, unsupported)

        # carry the author's layout on the model so a repair does not re-lay-out
        # an untouched diagram
        _attach_di(root, processes)

        diagram = BpmnDiagram(
            definitions_id=root.get("id", "definitions"),
            target_namespace=root.get("targetNamespace", "http://bpmn.io/schema/bpmn"),
            processes=processes,
            namespaces=ns,
        )
        return diagram, unsupported

    def serialize(self, diagram: BpmnDiagram) -> bytes:
        """Serialise a BpmnDiagram back to BPMN XML bytes."""
        nsmap = _normalise_nsmap(diagram.namespaces)
        _ensure_namespace(nsmap, None, BPMN_NS)
        _ensure_namespace(nsmap, "bpmndi", BPMNDI_NS)
        _ensure_namespace(nsmap, "dc", DC_NS)
        _ensure_namespace(nsmap, "di", DI_NS)
        # only declare xsi when a condition will use it, so an unconditioned
        # diagram does not pick up a namespace it never needs
        if _has_condition_expression(diagram):
            _ensure_namespace(nsmap, "xsi", XSI_NS)

        root = etree.Element(
            f"{{{BPMN_NS}}}definitions",
            nsmap=cast(Any, nsmap),
            attrib={
                "id": diagram.definitions_id,
                "targetNamespace": diagram.target_namespace,
            },
        )
        formal_expr_type = _formal_expression_type(nsmap)
        for proc in diagram.processes:
            root.append(_serialize_process(proc, BPMN_NS, formal_expr_type))

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


def _attach_di(root: etree._Element, processes: list[BpmnProcess]) -> None:
    """copy BPMNDI geometry onto the flow nodes and flows it belongs to

    BPMNDI lives in a separate subtree keyed by `bpmnElement`. Elements with no
    shape keep `bounds=None`, which the serialiser reads as "never placed".
    """
    shapes: dict[str, tuple[Bounds, Bounds | None]] = {}
    edges: dict[str, tuple[list[Waypoint], Bounds | None]] = {}

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        local = etree.QName(el.tag).localname
        target = el.get("bpmnElement")
        if not target:
            continue
        if local == "BPMNShape":
            bounds = _read_bounds(el.find(f"{{{DC_NS}}}Bounds"))
            if bounds is not None:
                shapes[target] = (bounds, _read_label_bounds(el))
        elif local == "BPMNEdge":
            waypoints = [
                Waypoint(x=float(wp.get("x", 0)), y=float(wp.get("y", 0)))
                for wp in el.findall(f"{{{DI_NS}}}waypoint")
            ]
            if waypoints:
                edges[target] = (waypoints, _read_label_bounds(el))

    for proc in processes:
        for node in proc.flow_nodes:
            found = shapes.get(node.id)
            if found is not None:
                node.bounds, node.label_bounds = found
        for flow in proc.sequence_flows:
            found_edge = edges.get(flow.id)
            if found_edge is not None:
                flow.waypoints, flow.label_bounds = found_edge


def _read_bounds(el: etree._Element | None) -> Bounds | None:
    if el is None:
        return None
    try:
        return Bounds(
            x=float(el.get("x", 0)),
            y=float(el.get("y", 0)),
            width=float(el.get("width", 0)),
            height=float(el.get("height", 0)),
        )
    except (TypeError, ValueError):
        return None


def _read_label_bounds(parent: etree._Element) -> Bounds | None:
    label = parent.find(f"{{{BPMNDI_NS}}}BPMNLabel")
    if label is None:
        return None
    return _read_bounds(label.find(f"{{{DC_NS}}}Bounds"))


def _parse_process(
    proc_el: etree._Element,
    unsupported: list[UnsupportedElement] | None = None,
) -> BpmnProcess:
    flow_nodes: list[FlowNode] = []
    sequence_flows: list[SequenceFlow] = []
    seen_ids: set[str] = set()
    proc_id = proc_el.get("id", "process_1")

    for child in proc_el:
        if not isinstance(child.tag, str):
            continue  # skip comment and processing-instruction nodes
        local = etree.QName(child.tag).localname
        if local in _FLOW_NODE_TAGS:
            node = _parse_flow_node(child, local, unsupported, proc_id)
            _reject_duplicate_id(node.id, proc_el, seen_ids)
            flow_nodes.append(node)
        elif local == "sequenceFlow":
            flow = _parse_sequence_flow(child)
            _reject_duplicate_id(flow.id, proc_el, seen_ids)
            sequence_flows.append(flow)
        elif unsupported is not None:
            unsupported.append(
                UnsupportedElement(
                    tag=local,
                    scope="process",
                    element_id=child.get("id"),
                    parent_id=proc_id,
                )
            )

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


def _collect_unsupported_definitions_children(
    root: etree._Element,
    unsupported: list[UnsupportedElement],
) -> None:
    """report document-level children that carry meaning the IR drops

    `collaboration` matters most — pools, participants and message flows live there.
    """
    root_id = root.get("id", "definitions")
    for child in root:
        if not isinstance(child.tag, str):
            continue
        local = etree.QName(child.tag).localname
        if local in _HANDLED_DEFINITIONS_TAGS or local in _IGNORED_DEFINITIONS_TAGS:
            continue
        unsupported.append(
            UnsupportedElement(
                tag=local,
                scope="definitions",
                element_id=child.get("id"),
                parent_id=root_id,
            )
        )


def _reject_duplicate_id(
    element_id: str, proc_el: etree._Element, seen_ids: set[str]
) -> None:
    if element_id in seen_ids:
        proc_id = proc_el.get("id", "process_1")
        raise ValueError(f"Duplicate element ID '{element_id}' in process '{proc_id}'.")
    seen_ids.add(element_id)


def _parse_flow_node(
    el: etree._Element,
    local_name: str,
    unsupported: list[UnsupportedElement] | None = None,
    proc_id: str | None = None,
) -> FlowNode:
    extra = {k: v for k, v in el.attrib.items() if k not in ("id", "name")}
    node_id = FlowNodeId(el.get("id") or "")
    event_definitions: list[EventDefinition] = []

    for child in el:
        if not isinstance(child.tag, str):
            continue
        local = etree.QName(child.tag).localname
        if local in _EVENT_DEFINITION_TAGS:
            event_definitions.append(_parse_event_definition(child, local))
            _report_children(child, unsupported, local, child.get("id") or node_id)
        elif local not in {"incoming", "outgoing"} and unsupported is not None:
            unsupported.append(
                UnsupportedElement(
                    tag=local,
                    scope=local_name,
                    element_id=child.get("id"),
                    parent_id=node_id or proc_id,
                )
            )

    return FlowNode(
        id=node_id,
        type=FlowNodeType(local_name),
        name=el.get("name"),
        event_definitions=event_definitions,
        extra=extra,
    )


def _parse_event_definition(
    el: etree._Element, local_name: str
) -> EventDefinition:
    return EventDefinition(
        type=EventDefinitionType(local_name),
        id=el.get("id"),
        extra={key: value for key, value in el.attrib.items() if key != "id"},
    )


def _report_children(
    parent: etree._Element,
    unsupported: list[UnsupportedElement] | None,
    scope: str,
    parent_id: str | None,
) -> None:
    if unsupported is None:
        return
    for child in parent:
        if not isinstance(child.tag, str):
            continue
        unsupported.append(
            UnsupportedElement(
                tag=etree.QName(child.tag).localname,
                scope=scope,
                element_id=child.get("id"),
                parent_id=parent_id,
            )
        )


def _parse_sequence_flow(el: etree._Element) -> SequenceFlow:
    cond_expr = None
    for child in el:
        if etree.QName(child.tag).localname == "conditionExpression":
            cond_expr = (child.text or "").strip()
    return SequenceFlow(
        id=SequenceFlowId(el.get("id") or ""),
        source_ref=FlowNodeId(el.get("sourceRef") or ""),
        target_ref=FlowNodeId(el.get("targetRef") or ""),
        name=el.get("name"),
        condition_expression=cond_expr,
    )


def _has_condition_expression(diagram: BpmnDiagram) -> bool:
    return any(
        sf.condition_expression
        for proc in diagram.processes
        for sf in proc.sequence_flows
    )


def _formal_expression_type(nsmap: dict[None | str, str]) -> str:
    """The `xsi:type` value to stamp on `conditionExpression`.

    A QName, so it must carry whatever prefix this document binds to the BPMN
    namespace, and no prefix when BPMN is the default namespace.
    """
    for prefix, uri in nsmap.items():
        if uri == BPMN_NS:
            return "tFormalExpression" if prefix is None else f"{prefix}:tFormalExpression"
    return "tFormalExpression"


def _serialize_process(
    proc: BpmnProcess, bpmn_ns: str, formal_expr_type: str
) -> etree._Element:
    attrib: dict[str, str] = {
        "id": proc.id,
        "isExecutable": str(proc.is_executable).lower(),
    }
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
        for definition in node.event_definitions:
            etree.SubElement(
                node_el,
                f"{{{bpmn_ns}}}{definition.type.value}",
                attrib={
                    **({"id": definition.id} if definition.id else {}),
                    **definition.extra,
                },
            )

    for sf in proc.sequence_flows:
        sf_attrib: dict[str, str] = {
            "id": sf.id,
            "sourceRef": sf.source_ref,
            "targetRef": sf.target_ref,
        }
        if sf.name:
            sf_attrib["name"] = sf.name
        sf_el = etree.SubElement(el, f"{{{bpmn_ns}}}sequenceFlow", attrib=sf_attrib)
        if sf.condition_expression:
            # BPMN 2.0 types conditionExpression as tExpression; some tools
            # ignore one that is not narrowed to tFormalExpression via xsi:type
            cond_el = etree.SubElement(
                sf_el,
                f"{{{bpmn_ns}}}conditionExpression",
                attrib={f"{{{XSI_NS}}}type": formal_expr_type},
            )
            cond_el.text = sf.condition_expression

    return el


def _serialize_bpmndi(proc: BpmnProcess) -> etree._Element:
    """Emit BPMNDI, preferring the geometry the author gave us.

    Stored `bounds` are kept exactly; only elements without any go through the
    fallback layout, so a one-flow repair never re-draws the whole diagram.
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

    node_positions = _node_positions(proc)

    for node in proc.flow_nodes:
        bounds = node_positions.get(node.id)
        if bounds is None:
            continue
        shape_el = etree.SubElement(
            plane_el,
            f"{{{BPMNDI_NS}}}BPMNShape",
            attrib={"id": f"{node.id}_di", "bpmnElement": node.id},
        )
        _write_bounds(shape_el, bounds)
        _write_label(shape_el, node.label_bounds)

    for sf in proc.sequence_flows:
        waypoints = sf.waypoints or _fallback_waypoints(sf, node_positions)
        if not waypoints:
            continue
        edge_el = etree.SubElement(
            plane_el,
            f"{{{BPMNDI_NS}}}BPMNEdge",
            attrib={"id": f"{sf.id}_di", "bpmnElement": sf.id},
        )
        for wp in waypoints:
            etree.SubElement(
                edge_el,
                f"{{{DI_NS}}}waypoint",
                attrib={"x": _num(wp.x), "y": _num(wp.y)},
            )
        _write_label(edge_el, sf.label_bounds)

    return diagram_el


def _node_positions(proc: BpmnProcess) -> dict[str, Bounds]:
    """stored bounds where we have them, generated ones where we do not"""
    positions = {node.id: node.bounds for node in proc.flow_nodes if node.bounds}
    unplaced = [node for node in proc.flow_nodes if node.bounds is None]
    if not unplaced:
        return positions

    # start right of everything already placed so new nodes never overlap
    x = max((b.x + b.width for b in positions.values()), default=150 - _H_GAP) + _H_GAP
    order = {node_id: i for i, node_id in enumerate(_topo_order(proc))}
    for node in sorted(unplaced, key=lambda n: order.get(n.id, len(order))):
        w, h = _node_dimensions(node)
        positions[node.id] = Bounds(x=x, y=_Y_CENTER - h // 2, width=w, height=h)
        x += w + _H_GAP
    return positions


def _fallback_waypoints(
    sf: SequenceFlow, positions: dict[str, Bounds]
) -> list[Waypoint]:
    """a straight line between two shapes, for a flow with no stored routing"""
    src = positions.get(sf.source_ref)
    tgt = positions.get(sf.target_ref)
    if src is None or tgt is None:
        return []
    return [
        Waypoint(x=src.x + src.width, y=src.y + src.height / 2),
        Waypoint(x=tgt.x, y=tgt.y + tgt.height / 2),
    ]


def _write_bounds(parent: etree._Element, bounds: Bounds) -> None:
    etree.SubElement(
        parent,
        f"{{{DC_NS}}}Bounds",
        attrib={
            "x": _num(bounds.x),
            "y": _num(bounds.y),
            "width": _num(bounds.width),
            "height": _num(bounds.height),
        },
    )


def _write_label(parent: etree._Element, bounds: Bounds | None) -> None:
    if bounds is None:
        return
    label_el = etree.SubElement(parent, f"{{{BPMNDI_NS}}}BPMNLabel")
    _write_bounds(label_el, bounds)


def _num(value: float) -> str:
    """BPMNDI coordinates are decimals, but whole numbers should stay whole"""
    return str(int(value)) if float(value).is_integer() else str(value)


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
