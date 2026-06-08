from app.model.schema import (
    BpmnDiagram,
    BpmnProcess,
    FlowNode,
    FlowNodeType,
    Lane,
    Pool,
    SequenceFlow,
)

BPMN_NAMESPACES = {
    "": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
}


def canonical_full_diagram() -> BpmnDiagram:
    diagram = canonical_xml_supported_diagram()
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
    start = FlowNode(
        id="start_1",
        type=FlowNodeType.START_EVENT,
        name="Start",
        outgoing=["flow_1"],
        extra={"customAttr": "kept"},
    )
    task = FlowNode(
        id="task_1",
        type=FlowNodeType.USER_TASK,
        name="Approve request",
        incoming=["flow_1"],
        outgoing=["flow_2"],
    )
    end = FlowNode(
        id="end_1",
        type=FlowNodeType.END_EVENT,
        name="Done",
        incoming=["flow_2"],
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
                    ),
                    SequenceFlow(
                        id="flow_2",
                        source_ref="task_1",
                        target_ref="end_1",
                        name="approved",
                        condition_expression="${approved}",
                    ),
                ],
            )
        ],
    )
