from app.experiments import ExperimentConfig
from app.model.schema import BpmnDiagram, BpmnProcess, FlowNode, FlowNodeType, SequenceFlow
from app.services import validation as validation_service
from app.validation.checkers import (
    _element_index,
    _petri_names,
    _resolve_petri_name,
    checker_versions,
    run_woflan,
)
from app.validation.rules import Severity, ValidationIssue


async def test_validate_diagram_merges_tier2_checker_issues(monkeypatch):
    async def fake_run_tier2_checkers(diagram, config):
        return [
            ValidationIssue(
                rule_id="woflan:soundness",
                severity=Severity.ERROR,
                message="Process can deadlock.",
                element_refs=["task_1"],
                source="woflan",
            )
        ]

    monkeypatch.setattr(
        validation_service,
        "run_tier2_checkers",
        fake_run_tier2_checkers,
    )

    result = await validation_service.validate_diagram(
        _minimal_valid_diagram(),
        config=ExperimentConfig.model_validate({"tiers_enabled": {"t2": True}}),
    )

    assert result.is_valid is False
    assert result.issues[0].rule_id == "woflan:soundness"
    assert result.issues[0].source == "woflan"
    assert result.issues[0].element_refs == ["task_1"]


async def test_validate_diagram_runs_tier2_with_tier1_precondition_errors(monkeypatch):
    checker_called = False

    async def fake_run_tier2_checkers(diagram, config):
        nonlocal checker_called
        checker_called = True
        return []

    monkeypatch.setattr(
        validation_service,
        "run_tier2_checkers",
        fake_run_tier2_checkers,
    )
    diagram = _minimal_valid_diagram()
    diagram.processes[0].flow_nodes.append(
        FlowNode(id="orphan_start", type=FlowNodeType.START_EVENT)
    )

    result = await validation_service.validate_diagram(
        diagram,
        config=ExperimentConfig.model_validate({"tiers_enabled": {"t2": True}}),
    )

    assert checker_called is True
    assert [issue.rule_id for issue in result.issues] == ["R003"]


def test_checker_versions_reports_enabled_selected_woflan():
    versions = checker_versions(
        ExperimentConfig.model_validate(
            {
                "tiers_enabled": {"t2": True},
                "t2_tools": ["woflan"],
            }
        )
    )

    assert versions["woflan"].startswith("pm4py-")


class _FakePetriObject:
    """stands in for a pm4py Place/Transition — id in `name`, label is cosmetic"""

    def __init__(self, name: str, label: str | None = None):
        self.name = name
        self.label = label


def test_resolve_petri_name_maps_encoded_names_back_to_element_ids():
    index = _element_index(_deadlock_diagram())

    # transitions carry the node id, sequence-flow places carry the flow id
    assert _resolve_petri_name("task_a", index) == "task_a"
    assert _resolve_petri_name("sf_4", index) == "sf_4"
    # the per-node entry/exit places and the invisible flow transitions
    assert _resolve_petri_name("ent_task_a", index) == "task_a"
    assert _resolve_petri_name("exi_xs", index) == "xs"
    assert _resolve_petri_name("sfl_sf_4", index) == "sf_4"
    assert _resolve_petri_name("tfl_sf_4", index) == "sf_4"


def test_resolve_petri_name_rejects_names_the_encoding_invented():
    index = _element_index(_deadlock_diagram())

    assert _resolve_petri_name("short_circuited_transition", index) is None
    assert _resolve_petri_name("source", index) is None
    assert _resolve_petri_name("sink", index) is None
    # pm4py names gateway split/join invisibles with a uuid4
    assert _resolve_petri_name("570f50fa-b555-42ef-9597-c5733b9d2a3f", index) is None
    # a prefix alone is not enough — the remainder has to be a real element
    assert _resolve_petri_name("exi_not_in_diagram", index) is None


def test_petri_names_reads_id_not_human_label():
    # pm4py puts the BPMN id in `name` and the task's display name in `label`;
    # reading `label` would yield something that matches no element id
    assert _petri_names([_FakePetriObject("task_a", "Do A")]) == ["task_a"]
    assert _petri_names(None) == []


def test_woflan_reports_real_element_ids_and_no_phantom_transition():
    """regression: Woflan's short-circuit transition must not reach element_refs

    the diagram deadlocks (XOR split feeding an AND join) and leaves `task_c`
    dead behind the join, so Woflan returns both its own synthetic transition
    and a genuine dead task in the same diagnostic list.
    """
    issues = run_woflan(_deadlock_diagram())

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "woflan:soundness"
    assert issue.severity == Severity.ERROR

    known_ids = _element_index(_deadlock_diagram()).ids
    assert issue.element_refs, "the violation should localise to some element"
    assert set(issue.element_refs) <= known_ids
    assert "short_circuited_transition" not in issue.element_refs

    # the dead task is found by id, not by its display name "Finalize"
    assert "task_c" in issue.element_refs
    assert "Finalize" not in issue.element_refs

    # the message talks about BPMN elements, not Petri-net places
    for petri_name in ("short_circuited_transition", "exi_xs", "source", "sink"):
        assert petri_name not in issue.message
        assert petri_name not in issue.formal_witness.description

    # pm4py's own names survive in raw so a run stays reproducible
    assert "short_circuited_transition" in issue.raw["petri_net_names"]["dead_tasks"]


def test_woflan_returns_no_issues_for_a_sound_process():
    assert run_woflan(_minimal_valid_diagram()) == []


def _deadlock_diagram() -> BpmnDiagram:
    """XOR split -> AND join, with a task stranded behind the join"""
    nodes = [
        FlowNode(id="start_1", type=FlowNodeType.START_EVENT, name="Start", outgoing=["sf_1"]),
        FlowNode(
            id="xs",
            type=FlowNodeType.EXCLUSIVE_GATEWAY,
            name="Choose",
            incoming=["sf_1"],
            outgoing=["sf_2", "sf_3"],
        ),
        FlowNode(id="task_a", type=FlowNodeType.TASK, name="Do A", incoming=["sf_2"], outgoing=["sf_4"]),
        FlowNode(id="task_b", type=FlowNodeType.TASK, name="Do B", incoming=["sf_3"], outgoing=["sf_5"]),
        FlowNode(
            id="aj",
            type=FlowNodeType.PARALLEL_GATEWAY,
            name="Sync",
            incoming=["sf_4", "sf_5"],
            outgoing=["sf_6"],
        ),
        FlowNode(id="task_c", type=FlowNodeType.TASK, name="Finalize", incoming=["sf_6"], outgoing=["sf_7"]),
        FlowNode(id="end_1", type=FlowNodeType.END_EVENT, name="End", incoming=["sf_7"]),
    ]
    flows = [
        SequenceFlow(id="sf_1", source_ref="start_1", target_ref="xs"),
        SequenceFlow(id="sf_2", source_ref="xs", target_ref="task_a"),
        SequenceFlow(id="sf_3", source_ref="xs", target_ref="task_b"),
        SequenceFlow(id="sf_4", source_ref="task_a", target_ref="aj"),
        SequenceFlow(id="sf_5", source_ref="task_b", target_ref="aj"),
        SequenceFlow(id="sf_6", source_ref="aj", target_ref="task_c"),
        SequenceFlow(id="sf_7", source_ref="task_c", target_ref="end_1"),
    ]
    return BpmnDiagram(
        definitions_id="def_1",
        processes=[BpmnProcess(id="proc_1", flow_nodes=nodes, sequence_flows=flows)],
    )


def _minimal_valid_diagram() -> BpmnDiagram:
    start = FlowNode(id="start_1", type=FlowNodeType.START_EVENT, outgoing=["sf_1"])
    task = FlowNode(
        id="task_1",
        type=FlowNodeType.TASK,
        incoming=["sf_1"],
        outgoing=["sf_2"],
    )
    end = FlowNode(id="end_1", type=FlowNodeType.END_EVENT, incoming=["sf_2"])
    flows = [
        SequenceFlow(id="sf_1", source_ref="start_1", target_ref="task_1"),
        SequenceFlow(id="sf_2", source_ref="task_1", target_ref="end_1"),
    ]
    return BpmnDiagram(
        definitions_id="def_1",
        processes=[BpmnProcess(id="proc_1", flow_nodes=[start, task, end], sequence_flows=flows)],
    )
