from app.model.schema import (
    BpmnDiagram,
    BpmnProcess,
    FlowNode,
    FlowNodeType,
    SequenceFlow,
)
from app.repair.ops import apply_edit_ops
from app.validation.rules import validate
from evaluation.generator.models import OperatorId
from evaluation.generator.operators import (
    soundness_candidate_injections,
    soundness_injections,
    soundness_sites,
)


def _gateway_region(gateway_type: FlowNodeType) -> BpmnDiagram:
    node_types = {
        "Start": FlowNodeType.START_EVENT,
        "Split": gateway_type,
        "A": FlowNodeType.TASK,
        "B": FlowNodeType.TASK,
        "Join": gateway_type,
        "End": FlowNodeType.END_EVENT,
    }
    edges = [
        ("Start", "Split"),
        ("Split", "A"),
        ("Split", "B"),
        ("A", "Join"),
        ("B", "Join"),
        ("Join", "End"),
    ]
    return BpmnDiagram(
        definitions_id="Defs",
        processes=[
            BpmnProcess(
                id="Process",
                flow_nodes=[
                    FlowNode(id=node_id, type=node_type)
                    for node_id, node_type in node_types.items()
                ],
                sequence_flows=[
                    SequenceFlow(
                        id=f"Flow_{index}", source_ref=source, target_ref=target
                    )
                    for index, (source, target) in enumerate(edges, start=1)
                ],
            )
        ],
    )


def test_xor_region_produces_reversible_f01():
    diagram = _gateway_region(FlowNodeType.EXCLUSIVE_GATEWAY)

    injections = soundness_candidate_injections(diagram)

    assert len(injections) == 1
    injection = injections[0]
    assert injection.operator is OperatorId.XOR_SPLIT_AND_JOIN
    assert injection.site == ("Split", "Join")
    join = next(
        node
        for node in injection.diagram.processes[0].flow_nodes
        if node.id == "Join"
    )
    assert join.type is FlowNodeType.PARALLEL_GATEWAY
    repaired, results = apply_edit_ops(injection.repair, injection.diagram)
    assert all(result.applied for result in results)
    assert repaired == diagram


def test_parallel_region_produces_reversible_f02():
    diagram = _gateway_region(FlowNodeType.PARALLEL_GATEWAY)

    injection = next(
        item
        for item in soundness_candidate_injections(diagram)
        if item.operator is OperatorId.AND_SPLIT_XOR_JOIN
    )

    assert injection.operator is OperatorId.AND_SPLIT_XOR_JOIN
    assert injection.expected_finding == "lack_of_synchronisation"
    assert injection.injection[0].new_type is FlowNodeType.EXCLUSIVE_GATEWAY
    assert injection.repair[0].new_type is FlowNodeType.PARALLEL_GATEWAY


def test_a_join_that_one_branch_can_bypass_is_not_matched():
    diagram = _gateway_region(FlowNodeType.EXCLUSIVE_GATEWAY)
    process = diagram.processes[0]
    process.sequence_flows.append(
        SequenceFlow(id="Bypass", source_ref="B", target_ref="End")
    )
    diagram = BpmnDiagram.model_validate(diagram.model_dump())

    assert soundness_sites(diagram) == []


def test_site_selection_is_reproducible_and_capped_per_operator_and_seed():
    first = _gateway_region(FlowNodeType.EXCLUSIVE_GATEWAY)
    second = _gateway_region(FlowNodeType.PARALLEL_GATEWAY).processes[0]
    second.id = "Process_2"
    for node in second.flow_nodes:
        node.id = f"Two_{node.id}"
    for flow in second.sequence_flows:
        flow.id = f"Two_{flow.id}"
        flow.source_ref = f"Two_{flow.source_ref}"
        flow.target_ref = f"Two_{flow.target_ref}"
    third = _gateway_region(FlowNodeType.EXCLUSIVE_GATEWAY).processes[0]
    third.id = "Process_3"
    for node in third.flow_nodes:
        node.id = f"Three_{node.id}"
    for flow in third.sequence_flows:
        flow.id = f"Three_{flow.id}"
        flow.source_ref = f"Three_{flow.source_ref}"
        flow.target_ref = f"Three_{flow.target_ref}"
    diagram = BpmnDiagram(
        definitions_id="Defs_many",
        processes=[first.processes[0], second, third],
    )

    selected_once = soundness_injections(
        diagram, seed_id="many", random_seed=42
    )
    selected_twice = soundness_injections(
        diagram, seed_id="many", random_seed=42
    )

    assert len(selected_once) == 3
    assert len({item.operator for item in selected_once}) == 3
    assert [item.site for item in selected_once] == [
        item.site for item in selected_twice
    ]


def test_parallel_join_before_end_produces_reversible_f03():
    diagram = _gateway_region(FlowNodeType.PARALLEL_GATEWAY)

    injection = next(
        item
        for item in soundness_candidate_injections(diagram)
        if item.operator is OperatorId.DELETE_PARALLEL_JOIN
    )

    assert injection.site == ("Split", "Join", "End")
    assert all(node.id != "Join" for node in injection.diagram.processes[0].flow_nodes)
    end_inputs = {
        flow.source_ref
        for flow in injection.diagram.processes[0].sequence_flows
        if flow.target_ref == "End"
    }
    assert end_inputs == {"A", "B"}
    repaired, results = apply_edit_ops(injection.repair, injection.diagram)
    assert all(result.applied for result in results)
    assert _control_flow(repaired) == _control_flow(diagram)


def test_bridge_flow_produces_reversible_f04():
    diagram = BpmnDiagram(
        definitions_id="Defs_linear",
        processes=[
            BpmnProcess(
                id="Process",
                flow_nodes=[
                    FlowNode(id="Start", type=FlowNodeType.START_EVENT),
                    FlowNode(id="A", type=FlowNodeType.TASK),
                    FlowNode(id="B", type=FlowNodeType.TASK),
                    FlowNode(id="End", type=FlowNodeType.END_EVENT),
                ],
                sequence_flows=[
                    SequenceFlow(id="Flow_1", source_ref="Start", target_ref="A"),
                    SequenceFlow(id="Flow_2", source_ref="A", target_ref="B"),
                    SequenceFlow(id="Flow_3", source_ref="B", target_ref="End"),
                ],
            )
        ],
    )

    injection = next(
        item
        for item in soundness_candidate_injections(diagram)
        if item.operator is OperatorId.DELETE_BRIDGE_FLOW
        and item.site == ("Flow_2", "A", "B")
    )

    assert injection.expected_finding == "R007"
    assert {issue.rule_id for issue in validate(injection.diagram).issues} == {
        "R007",
        "R008",
    }
    repaired, results = apply_edit_ops(injection.repair, injection.diagram)
    assert all(result.applied for result in results)
    assert _control_flow(repaired) == _control_flow(diagram)


def _control_flow(diagram: BpmnDiagram) -> tuple:
    process = diagram.processes[0]
    nodes = sorted((node.id, node.type, node.name) for node in process.flow_nodes)
    flows = sorted(
        (
            flow.id,
            flow.source_ref,
            flow.target_ref,
            flow.name,
            flow.condition_expression,
        )
        for flow in process.sequence_flows
    )
    return nodes, flows
