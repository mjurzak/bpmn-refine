"""Pin the §6.1 case study diagram to the findings the thesis text quotes."""
from pathlib import Path

from app.experiments import ExperimentConfig
from app.model.formats.pydantic_ir import PydanticConverter
from app.repair.ops import AddFlowOp, ChangeGatewayTypeOp, apply_edit_ops
from app.services.repair import dispatch_repair
from app.services import validation as validation_service
from app.services.validation import validate_diagram
from app.validation.checkers import run_woflan
from app.validation.rules import validate

CASE_STUDY = Path("data/test_cases/03_expense_reimbursement.bpmn")

# planted faults F1 and F2, described in the diagram's own header comment
EXPECTED_TIER1 = {
    ("R003", "start_card_feed"),
    ("R004", "end_escalated"),
    ("R008", "task_audit"),
}


def _diagram():
    return PydanticConverter().parse(CASE_STUDY.read_bytes())


def test_case_study_round_trips():
    converter = PydanticConverter()
    diagram = _diagram()
    assert converter.parse(converter.serialize(diagram)) == diagram


def test_case_study_fires_the_expected_tier1_rules():
    issues = validate(_diagram()).issues
    assert {(i.rule_id, i.element_id) for i in issues} == EXPECTED_TIER1


def test_case_study_fails_woflan_workflow_net_precondition():
    """The planted connectivity faults create extra Petri-net boundaries."""
    issues = run_woflan(_diagram())
    assert [i.rule_id for i in issues] == ["woflan:soundness"]
    assert issues[0].severity == "error"
    assert issues[0].tier == "tier2"
    assert issues[0].element_refs == []
    assert "more than one source place" in issues[0].message
    traces = issues[0].formal_witness.counterexample_traces
    assert traces == []
    known_ids = {
        element.id
        for process in _diagram().processes
        for element in [*process.flow_nodes, *process.sequence_flows]
    }
    assert all(step.fired in known_ids for trace in traces for step in trace)
    assert issues[0].formal_witness.description == issues[0].message


async def test_deep_validation_reports_formal_detail_after_connectivity_repairs(
    monkeypatch,
):
    async def run_woflan_without_worker_thread(diagram, config):
        return run_woflan(diagram)

    monkeypatch.setattr(
        validation_service,
        "run_tier2_checkers",
        run_woflan_without_worker_thread,
    )
    config = ExperimentConfig.model_validate({"tiers_enabled": {"t2": True}})

    initial = await validate_diagram(_diagram(), config=config)

    assert {issue.rule_id for issue in initial.issues} == {
        "R003",
        "R004",
        "R008",
        "woflan:soundness",
    }
    initial_formal = next(
        issue for issue in initial.issues if issue.source == "woflan"
    )
    assert initial_formal.element_refs == []
    assert "more than one source place" in initial_formal.message

    connected, op_results = apply_edit_ops(
        [
            AddFlowOp(
                process_id="Process_expense_reimbursement",
                id="sf_card_feed",
                source_ref="start_card_feed",
                target_ref="task_check",
            ),
            AddFlowOp(
                process_id="Process_expense_reimbursement",
                id="sf_audit_escalated",
                source_ref="task_audit",
                target_ref="end_escalated",
            ),
        ],
        _diagram(),
    )
    assert all(result.applied for result in op_results)

    repaired = await validate_diagram(connected, config=config)

    assert [issue.rule_id for issue in repaired.issues] == ["woflan:soundness"]
    assert repaired.issues[0].element_refs
    assert "not covered by an S-component" in repaired.issues[0].message


async def test_case_study_repairs_all_current_errors_in_one_llm_plan(monkeypatch):
    async def run_woflan_without_worker_thread(diagram, config):
        return run_woflan(diagram)

    monkeypatch.setattr(
        validation_service,
        "run_tier2_checkers",
        run_woflan_without_worker_thread,
    )
    config = ExperimentConfig.model_validate({"tiers_enabled": {"t2": True}})
    initial = await validate_diagram(_diagram(), config=config)
    assigned: list[list[str]] = []

    async def fake_atomic_repair(diagram, issues, **kwargs):
        assigned.append([issue.rule_id for issue in issues])
        return [
            AddFlowOp(
                process_id="Process_expense_reimbursement",
                id="sf_card_feed",
                source_ref="start_card_feed",
                target_ref="task_check",
            ),
            AddFlowOp(
                process_id="Process_expense_reimbursement",
                id="sf_audit_escalated",
                source_ref="task_audit",
                target_ref="end_escalated",
            ),
            ChangeGatewayTypeOp(
                id="gw_close",
                new_type="exclusiveGateway",
            ),
        ]

    result = await dispatch_repair(
        _diagram(),
        issues=initial.issues,
        config=config,
        atomic_repair_fn=fake_atomic_repair,
    )

    assert assigned == [["R003", "R004", "R008", "woflan:soundness"]]
    assert result.iterations == 1
    assert result.converged is True
    assert [op.op for op in result.applied_ops] == [
        "add_flow",
        "add_flow",
        "change_gateway_type",
    ]


def test_implicit_merge_is_not_flagged():
    """Negative control: task_check's two incoming flows are legal."""
    issues = validate(_diagram()).issues
    assert not any(issue.element_id == "task_check" for issue in issues)
