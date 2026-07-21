from app.model.schema import (
    Bounds,
    BpmnDiagram,
    BpmnProcess,
    FlowNode,
    FlowNodeType,
    Lane,
    Pool,
    SequenceFlow,
    Waypoint,
)

BPMN_NAMESPACES = {
    "": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
}


def canonical_full_diagram() -> BpmnDiagram:
    """the fixture for the lossy candidate IRs (mermaid, compact-json, yaml)

    Layout is stripped: those formats have no way to express geometry, so
    carrying bounds here would assert a round-trip they cannot satisfy. The
    XML converter is the only one that owns layout, and it is exercised by
    canonical_xml_supported_diagram.
    """
    diagram = canonical_xml_supported_diagram()
    for node in diagram.processes[0].flow_nodes:
        node.bounds = None
        node.label_bounds = None
    for flow in diagram.processes[0].sequence_flows:
        flow.waypoints = []
        flow.label_bounds = None
    diagram.processes[0].pools = [
        Pool(
            id="pool_1",
            name="Operations",
            lanes=[
                Lane(
                    id="lane_1",
                    name="Reviewer",
                    flow_node_refs=["task_1"],
                )
            ],
        )
    ]
    return diagram


def canonical_xml_supported_diagram() -> BpmnDiagram:
    # geometry is part of what BPMN XML supports, so the round-trip fixture
    # carries it. an IR *without* bounds cannot round-trip exactly — serialising
    # has to invent a layout or bpmn-js has nothing to render; see
    # test_layout_is_generated_only_when_absent
    start = FlowNode(
        id="start_1",
        type=FlowNodeType.START_EVENT,
        name="Start",
        outgoing=["flow_1"],
        bounds=Bounds(x=150, y=100, width=36, height=36),
        extra={"customAttr": "kept"},
    )
    task = FlowNode(
        id="task_1",
        type=FlowNodeType.USER_TASK,
        name="Approve request",
        incoming=["flow_1"],
        outgoing=["flow_2"],
        bounds=Bounds(x=240, y=78, width=100, height=80),
    )
    end = FlowNode(
        id="end_1",
        type=FlowNodeType.END_EVENT,
        name="Done",
        incoming=["flow_2"],
        bounds=Bounds(x=400, y=100, width=36, height=36),
    )
    return BpmnDiagram(
        definitions_id="definitions_1",
        target_namespace="http://example.test/bpmn",
        namespaces=BPMN_NAMESPACES,
        processes=[
            BpmnProcess(
                id="process_1",
                name="Request process",
                is_executable=True,
                flow_nodes=[start, task, end],
                sequence_flows=[
                    SequenceFlow(
                        id="flow_1",
                        source_ref="start_1",
                        target_ref="task_1",
                        waypoints=[Waypoint(x=186, y=118), Waypoint(x=240, y=118)],
                    ),
                    SequenceFlow(
                        id="flow_2",
                        source_ref="task_1",
                        target_ref="end_1",
                        name="approved",
                        condition_expression="${approved}",
                        waypoints=[Waypoint(x=340, y=118), Waypoint(x=400, y=118)],
                    ),
                ],
            )
        ],
    )
