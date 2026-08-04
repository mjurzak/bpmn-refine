from app.model.schema import BpmnDiagram, BpmnProcess, FlowNode, FlowNodeType, SequenceFlow
from app.repair.ops import apply_edit_ops
from app.repair.quick_fixes import propose_quick_fix
from app.services.repair import _batch_quick_fixes, _highest_priority_batch
from app.validation.rules import validate


def test_a_batch_never_proposes_the_same_fix_twice():
    """R005 and R006 fire together on a flow with two unknown endpoints

    Both resolve to the same `remove_flow`; the duplicate used to fail on apply
    and surface as a broken deterministic fix.
    """
    diagram = _diagram_with_doubly_dangling_flow()
    issues = validate(diagram).errors()

    assert sorted(issue.rule_id for issue in issues) == ["R005", "R006"]

    ops = _batch_quick_fixes(_highest_priority_batch(issues), diagram)
    updated, results = apply_edit_ops(ops or [], diagram)

    assert [op.op for op in ops or []] == ["remove_flow"]
    assert all(result.applied for result in results)
    assert validate(updated).is_valid is True


def _diagram_with_doubly_dangling_flow() -> BpmnDiagram:
    return BpmnDiagram(
        definitions_id="defs_1",
        processes=[
            BpmnProcess(
                id="proc_1",
                flow_nodes=[
                    FlowNode(id="start_1", type=FlowNodeType.START_EVENT),
                    FlowNode(id="end_1", type=FlowNodeType.END_EVENT),
                ],
                sequence_flows=[
                    SequenceFlow(id="sf_1", source_ref="start_1", target_ref="end_1"),
                    SequenceFlow(id="sf_bad", source_ref="ghost_a", target_ref="ghost_b"),
                ],
            )
        ],
    )


def test_quick_fix_adds_missing_start_event_and_entry_flow():
    diagram = _diagram_without_start()
    issue = validate(diagram).errors()[0]

    ops = propose_quick_fix(issue, diagram)
    updated, results = apply_edit_ops(ops or [], diagram)

    assert [op.op for op in ops or []] == ["add_node", "add_flow"]
    assert all(result.applied for result in results)
    assert validate(updated).is_valid is True


def test_quick_fix_removes_sequence_flow_with_unknown_target():
    diagram = _diagram_with_dangling_flow()
    issue = next(item for item in validate(diagram).errors() if item.rule_id == "R006")

    ops = propose_quick_fix(issue, diagram)
    updated, results = apply_edit_ops(ops or [], diagram)

    assert [op.op for op in ops or []] == ["remove_flow"]
    assert all(result.applied for result in results)
    assert validate(updated).is_valid is True
    assert {flow.id for flow in updated.processes[0].sequence_flows} == {"sf_1"}


def test_quick_fix_declines_to_feed_start_event():
    """R003 is unregistered — wiring an orphan start event is the LLM's call"""
    diagram = _diagram_with_orphan_start()
    issue = next(item for item in validate(diagram).errors() if item.rule_id == "R003")

    ops = propose_quick_fix(issue, diagram)

    assert ops is None


def test_quick_fix_declines_to_fork_start_to_orphan_end():
    """R004 is unregistered — deleting vs connecting needs the whole diagram"""
    diagram = _diagram_with_orphan_end()
    issue = next(item for item in validate(diagram).errors() if item.rule_id == "R004")

    ops = propose_quick_fix(issue, diagram)

    assert ops is None


def test_quick_fix_never_staples_two_orphans_together():
    """the case 09 regression: R003 and R004 both used to return the same op

    An orphan start and an orphan end event in one process is exactly the shape
    the old heuristic got wrong — each rule's fix picked the other rule's element
    as its endpoint, joining two defects into a plausible-looking flow.
    """
    from pathlib import Path

    from app.model.formats.pydantic_ir import PydanticConverter

    diagram = PydanticConverter().parse(
        Path("data/test_cases/03_expense_reimbursement.bpmn").read_bytes()
    )
    connectivity = [
        issue
        for issue in validate(diagram).issues
        if issue.rule_id in {"R003", "R004"}
    ]
    assert {issue.rule_id for issue in connectivity} == {"R003", "R004"}
    assert all(propose_quick_fix(issue, diagram) is None for issue in connectivity)


def _diagram_without_start() -> BpmnDiagram:
    task = FlowNode(id="task_1", type=FlowNodeType.TASK, outgoing=["sf_1"])
    end = FlowNode(id="end_1", type=FlowNodeType.END_EVENT, incoming=["sf_1"])
    flow = SequenceFlow(id="sf_1", source_ref="task_1", target_ref="end_1")
    return BpmnDiagram(
        definitions_id="def_1",
        processes=[BpmnProcess(id="proc_1", flow_nodes=[task, end], sequence_flows=[flow])],
    )


def _diagram_with_dangling_flow() -> BpmnDiagram:
    start = FlowNode(id="start_1", type=FlowNodeType.START_EVENT, outgoing=["sf_1", "sf_bad"])
    end = FlowNode(id="end_1", type=FlowNodeType.END_EVENT, incoming=["sf_1"])
    flows = [
        SequenceFlow(id="sf_1", source_ref="start_1", target_ref="end_1"),
        SequenceFlow(id="sf_bad", source_ref="start_1", target_ref="missing_node"),
    ]
    return BpmnDiagram(
        definitions_id="def_1",
        processes=[BpmnProcess(id="proc_1", flow_nodes=[start, end], sequence_flows=flows)],
    )


def _diagram_with_orphan_start() -> BpmnDiagram:
    start_main = FlowNode(
        id="start_main", type=FlowNodeType.START_EVENT, outgoing=["sf_1"]
    )
    task = FlowNode(
        id="task_review",
        type=FlowNodeType.TASK,
        incoming=["sf_1"],
        outgoing=["sf_2"],
    )
    end = FlowNode(id="end_done", type=FlowNodeType.END_EVENT, incoming=["sf_2"])
    start_orphan = FlowNode(id="start_emergency", type=FlowNodeType.START_EVENT)
    flows = [
        SequenceFlow(id="sf_1", source_ref="start_main", target_ref="task_review"),
        SequenceFlow(id="sf_2", source_ref="task_review", target_ref="end_done"),
    ]
    return BpmnDiagram(
        definitions_id="def_1",
        processes=[
            BpmnProcess(
                id="proc_1",
                flow_nodes=[start_main, task, end, start_orphan],
                sequence_flows=flows,
            )
        ],
    )


def _diagram_with_orphan_end() -> BpmnDiagram:
    start = FlowNode(id="start_in", type=FlowNodeType.START_EVENT, outgoing=["sf_1"])
    task = FlowNode(
        id="task_main",
        type=FlowNodeType.TASK,
        incoming=["sf_1"],
        outgoing=["sf_2"],
    )
    end_done = FlowNode(id="end_done", type=FlowNodeType.END_EVENT, incoming=["sf_2"])
    end_cancelled = FlowNode(id="end_cancelled", type=FlowNodeType.END_EVENT)
    flows = [
        SequenceFlow(id="sf_1", source_ref="start_in", target_ref="task_main"),
        SequenceFlow(id="sf_2", source_ref="task_main", target_ref="end_done"),
    ]
    return BpmnDiagram(
        definitions_id="def_1",
        processes=[
            BpmnProcess(
                id="proc_1",
                flow_nodes=[start, task, end_done, end_cancelled],
                sequence_flows=flows,
            )
        ],
    )
