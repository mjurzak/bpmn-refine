"""
convert a Signavio JSON model (from SAP-SAM CSV) to BPMN 2.0 XML

maps Signavio stencil types to the project IR (BpmnDiagram), then serializes
using the existing PydanticConverter so the output is identical in structure
to what a user uploads through the UI.

pools, lanes, annotations, data objects and message flows are dropped; the IR
covers the executable flow graph only.

the JSON traversal in _get_elements_flat is adapted from BpmnModelParser in
the SAP-SAM project (https://github.com/signavio/sap-sam, Apache 2.0,
Copyright 2022 SAP). the stencil-to-IR mapping and BPMN serialization are
original to this project.

usage:
    python scripts/signavio_to_bpmn.py data/sample_model.json out.bpmn
    python scripts/signavio_to_bpmn.py data/sample_model.json -  # stdout
"""

import json
import sys
from collections import deque
from pathlib import Path

# allow imports from backend/app without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.model.formats.pydantic_ir import PydanticConverter
from app.model.schema import BpmnDiagram, BpmnProcess, FlowNode, FlowNodeType, SequenceFlow

# stencil IDs that are edges (not flow nodes)
_EDGES = frozenset({
    "SequenceFlow",
    "Association_Undirected",
    "Association_Unidirectional",
    "Association_Bidirectional",
    "MessageFlow",
})

# stencil IDs we drop entirely (layout / annotation / data artifacts)
_SKIP = frozenset({
    "Pool", "CollapsedPool", "VerticalPool", "CollapsedVerticalPool",
    "Lane", "VerticalLane", "processparticipant",
    "TextAnnotation", "Group", "ITSystem",
    "DataObject", "DataStore", "Message",
    "BPMNDiagram",
})

# signavio stencil ID → project FlowNodeType
_STENCIL_MAP: dict[str, FlowNodeType] = {
    "Task":                                  FlowNodeType.TASK,
    "Subprocess":                            FlowNodeType.SUB_PROCESS,
    "CollapsedSubprocess":                   FlowNodeType.SUB_PROCESS,
    "EventSubprocess":                       FlowNodeType.SUB_PROCESS,
    "CollapsedEventSubprocess":              FlowNodeType.SUB_PROCESS,
    "Exclusive_Databased_Gateway":           FlowNodeType.EXCLUSIVE_GATEWAY,
    "EventbasedGateway":                     FlowNodeType.EVENT_BASED_GATEWAY,
    "ParallelGateway":                       FlowNodeType.PARALLEL_GATEWAY,
    "InclusiveGateway":                      FlowNodeType.INCLUSIVE_GATEWAY,
    "ComplexGateway":                        FlowNodeType.COMPLEX_GATEWAY,
    # start events
    "StartNoneEvent":                        FlowNodeType.START_EVENT,
    "StartMessageEvent":                     FlowNodeType.START_EVENT,
    "StartTimerEvent":                       FlowNodeType.START_EVENT,
    "StartEscalationEvent":                  FlowNodeType.START_EVENT,
    "StartConditionalEvent":                 FlowNodeType.START_EVENT,
    "StartErrorEvent":                       FlowNodeType.START_EVENT,
    "StartCompensationEvent":                FlowNodeType.START_EVENT,
    "StartSignalEvent":                      FlowNodeType.START_EVENT,
    "StartMultipleEvent":                    FlowNodeType.START_EVENT,
    "StartParallelMultipleEvent":            FlowNodeType.START_EVENT,
    # intermediate catch events
    "IntermediateEvent":                     FlowNodeType.INTERMEDIATE_CATCH_EVENT,
    "IntermediateMessageEventCatching":      FlowNodeType.INTERMEDIATE_CATCH_EVENT,
    "IntermediateTimerEvent":                FlowNodeType.INTERMEDIATE_CATCH_EVENT,
    "IntermediateEscalationEvent":           FlowNodeType.INTERMEDIATE_CATCH_EVENT,
    "IntermediateConditionalEvent":          FlowNodeType.INTERMEDIATE_CATCH_EVENT,
    "IntermediateLinkEventCatching":         FlowNodeType.INTERMEDIATE_CATCH_EVENT,
    "IntermediateErrorEvent":                FlowNodeType.INTERMEDIATE_CATCH_EVENT,
    "IntermediateCancelEvent":               FlowNodeType.INTERMEDIATE_CATCH_EVENT,
    "IntermediateCompensationEventCatching": FlowNodeType.INTERMEDIATE_CATCH_EVENT,
    "IntermediateSignalEventCatching":       FlowNodeType.INTERMEDIATE_CATCH_EVENT,
    "IntermediateMultipleEventCatching":     FlowNodeType.INTERMEDIATE_CATCH_EVENT,
    "IntermediateParallelMultipleEventCatching": FlowNodeType.INTERMEDIATE_CATCH_EVENT,
    # intermediate throw events
    "IntermediateMessageEventThrowing":      FlowNodeType.INTERMEDIATE_THROW_EVENT,
    "IntermediateEscalationEventThrowing":   FlowNodeType.INTERMEDIATE_THROW_EVENT,
    "IntermediateLinkEventThrowing":         FlowNodeType.INTERMEDIATE_THROW_EVENT,
    "IntermediateCompensationEventThrowing": FlowNodeType.INTERMEDIATE_THROW_EVENT,
    "IntermediateSignalEventThrowing":       FlowNodeType.INTERMEDIATE_THROW_EVENT,
    "IntermediateMultipleEventThrowing":     FlowNodeType.INTERMEDIATE_THROW_EVENT,
    # end events
    "EndNoneEvent":                          FlowNodeType.END_EVENT,
    "EndMessageEvent":                       FlowNodeType.END_EVENT,
    "EndEscalationEvent":                    FlowNodeType.END_EVENT,
    "EndErrorEvent":                         FlowNodeType.END_EVENT,
    "EndCancelEvent":                        FlowNodeType.END_EVENT,
    "EndCompensationEvent":                  FlowNodeType.END_EVENT,
    "EndSignalEvent":                        FlowNodeType.END_EVENT,
    "EndMultipleEvent":                      FlowNodeType.END_EVENT,
    "EndTerminateEvent":                     FlowNodeType.END_EVENT,
}


# adapted from BpmnModelParser._get_elements_flat in SAP-SAM
# https://github.com/signavio/sap-sam/blob/main/src/sapsam/parser.py
# Copyright 2022 SAP, Apache License 2.0
def _get_elements_flat(model_dict: dict) -> list[dict]:
    """DFS traversal of childShapes; returns structured element records"""
    stack = deque([model_dict])
    elements = []
    while stack:
        element = stack.pop()
        for c in element.get("childShapes", []):
            stack.append(c)
        if element["resourceId"] == model_dict["resourceId"]:
            continue
        elements.append({
            "element_id": element["resourceId"],
            "category": element.get("stencil", {}).get("id"),
            "label": element.get("properties", {}).get("name"),
            "properties": element.get("properties", {}),
            # flatten outgoing list-of-dicts to a plain list of IDs (SAP-SAM pattern)
            "outgoing": [v for d in element.get("outgoing", []) for v in d.values()],
        })
    return elements


def signavio_to_diagram(model_json: dict) -> BpmnDiagram:
    """convert a parsed Signavio JSON dict to a BpmnDiagram IR object"""
    elements = _get_elements_flat(model_json)

    # map each edge ID back to the node that lists it in outgoing, rebuilding sourceRef
    edge_to_source: dict[str, str] = {}
    for el in elements:
        if el["category"] in _SKIP:
            continue
        for edge_id in el["outgoing"]:
            edge_to_source[edge_id] = el["element_id"]

    flow_nodes: list[FlowNode] = []
    sequence_flows: list[SequenceFlow] = []

    for el in elements:
        cat = el["category"]
        eid = el["element_id"]
        props = el["properties"]

        if cat in _SKIP:
            continue

        if cat == "SequenceFlow":
            source = edge_to_source.get(eid)
            outgoing = el["outgoing"]
            if not source or not outgoing:
                continue
            name_raw = props.get("name") or props.get("label") or None
            cond = props.get("conditionexpression") or props.get("condition") or None
            sequence_flows.append(SequenceFlow(
                id=eid,
                source_ref=source,
                target_ref=outgoing[0],
                name=name_raw if name_raw else None,
                condition_expression=cond if cond else None,
            ))
            continue

        if cat in _EDGES:
            continue  # associations and message flows are not part of the flow graph

        node_type = _STENCIL_MAP.get(cat)
        if node_type is None:
            continue  # unknown stencil

        name_raw = el["label"] or props.get("label") or None
        flow_nodes.append(FlowNode(id=eid, type=node_type, name=name_raw))

    # keep only sequence flows whose source and target are both known flow nodes
    node_ids = {n.id for n in flow_nodes}
    sequence_flows = [
        sf for sf in sequence_flows
        if sf.source_ref in node_ids and sf.target_ref in node_ids
    ]

    # wire incoming / outgoing references on nodes
    node_index = {n.id: n for n in flow_nodes}
    for sf in sequence_flows:
        node_index[sf.source_ref].outgoing.append(sf.id)
        node_index[sf.target_ref].incoming.append(sf.id)

    process = BpmnProcess(id="process_1", flow_nodes=flow_nodes, sequence_flows=sequence_flows)
    return BpmnDiagram(definitions_id="definitions", processes=[process])


def convert(input_path: Path, output_path: Path | None) -> None:
    model_json = json.loads(input_path.read_text(encoding="utf-8"))
    diagram = signavio_to_diagram(model_json)
    xml_bytes = PydanticConverter().serialize(diagram)

    proc = diagram.processes[0] if diagram.processes else None
    nodes = len(proc.flow_nodes) if proc else 0
    flows = len(proc.sequence_flows) if proc else 0
    print(f"{nodes} flow nodes, {flows} sequence flows", file=sys.stderr)

    if output_path is None:
        sys.stdout.buffer.write(xml_bytes)
    else:
        output_path.write_bytes(xml_bytes)
        print(f"saved -> {output_path}", file=sys.stderr)


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: signavio_to_bpmn.py <input.json> [output.bpmn | -]", file=sys.stderr)
        sys.exit(1)

    input_path = Path(sys.argv[1])
    raw_out = sys.argv[2] if len(sys.argv) > 2 else "out.bpmn"
    output_path = None if raw_out == "-" else Path(raw_out)

    convert(input_path, output_path)


if __name__ == "__main__":
    main()
