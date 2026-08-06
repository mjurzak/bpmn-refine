import json
from pathlib import Path
from typing import Any

import pytest
from app.experiments import ExperimentConfig
from app.model.formats.pydantic_ir import PydanticConverter
from app.model.schema import (
    BpmnDiagram,
    BpmnProcess,
    FlowNode,
    FlowNodeType,
    SequenceFlow,
)
from app.repair.ops import RenameNodeOp
from app.services import repair as repair_service
from app.services.repair import (
    OpOrigin,
    RepairResult,
    StopReason,
    dispatch_repair,
    repair_with_edit_ops,
)
from app.services.validation import ValidationResult
from app.validation.rules import (
    FormalWitness,
    Severity,
    TraceStep,
    ValidationIssue,
    ValidationTier,
)


def _atomic_response(ops: list[dict[str, Any]]) -> dict[str, Any]:
    return {"description": "Atomic repair plan.", "result": {"ops": ops}}


async def test_dispatch_repair_prefers_quick_fix_over_llm():
    calls = []

    async def fake_repair(diagram, issues, **kwargs):
        calls.append(issues)
        return RepairResult(repaired_diagram=_minimal_valid_diagram())

    result = await dispatch_repair(
        _diagram_without_start(),
        issues=[
            ValidationIssue(
                rule_id="R001",
                severity=Severity.ERROR,
                message="Process has no start event.",
            )
        ],
        repair_fn=fake_repair,
    )

    assert result.iterations == 1
    assert result.converged is True
    assert result.remaining_issues == []
    assert [op.op for op in result.applied_ops] == ["add_node", "add_flow"]
    assert result.applied_op_origins == [OpOrigin.QUICK_FIX, OpOrigin.QUICK_FIX]
    assert calls == []


async def test_dispatch_repair_uses_atomic_llm_ops_when_no_quick_fix_exists():
    calls = []

    async def fake_atomic_repair(diagram, issues, **kwargs):
        calls.append((diagram, issues, kwargs))
        return [RenameNodeOp(id="task_1", new_name="Renamed task")]

    result = await dispatch_repair(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id="R999",
                severity=Severity.ERROR,
                message="Synthetic issue without a deterministic quick fix.",
                element_id="task_1",
            )
        ],
        atomic_repair_fn=fake_atomic_repair,
    )

    assert result.iterations == 1
    assert result.converged is True
    assert result.remaining_issues == []
    assert [op.op for op in result.applied_ops] == ["rename_node"]
    assert result.applied_op_origins == [OpOrigin.MODEL_PLAN]
    assert calls[0][1][0].rule_id == "R999"


async def test_dispatch_repair_batches_errors_then_retries_only_what_remains(
    monkeypatch,
):
    assigned: list[list[str]] = []
    validation_round = 0
    second_issue = ValidationIssue(
        rule_id="R998",
        severity=Severity.ERROR,
        message="Second synthetic issue.",
        element_id="task_1",
    )

    async def fake_atomic_repair(diagram, issues, **kwargs):
        assigned.append([issue.rule_id for issue in issues])
        return [RenameNodeOp(id="task_1", new_name=f"Fixed round {len(assigned)}")]

    async def fake_validate(diagram, **kwargs):
        nonlocal validation_round
        validation_round += 1
        remaining = [second_issue] if validation_round == 1 else []
        return ValidationResult(
            is_valid=not remaining,
            issues=remaining,
            semantic_issues=[],
        )

    monkeypatch.setattr(repair_service, "validate_diagram", fake_validate)
    result = await dispatch_repair(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id="R999",
                severity=Severity.ERROR,
                message="First synthetic issue.",
                element_id="task_1",
            ),
            second_issue,
        ],
        atomic_repair_fn=fake_atomic_repair,
    )

    assert assigned == [["R999", "R998"], ["R998"]]
    assert result.iterations == 2
    assert result.converged is True
    assert len(result.applied_ops) == 2


async def test_single_plan_targets_errors_before_warnings_without_chasing_new_ones(
    monkeypatch,
):
    assigned: list[list[str]] = []
    context: list[list[str]] = []
    validation_calls = 0
    newly_discovered = ValidationIssue(
        rule_id="semantic:missing_exception_handling",
        severity=Severity.WARNING,
        message="A newly discovered semantic concern.",
        tier=ValidationTier.TIER3,
        element_id="task_1",
    )

    async def fake_atomic_repair(diagram, issues, **kwargs):
        assigned.append([issue.rule_id for issue in issues])
        context.append([issue.rule_id for issue in kwargs["context_issues"]])
        return [RenameNodeOp(id="task_1", new_name="Proposed repair")]

    async def fake_validate(diagram, **kwargs):
        nonlocal validation_calls
        validation_calls += 1
        return ValidationResult(
            is_valid=True,
            issues=[],
            semantic_issues=[newly_discovered],
        )

    monkeypatch.setattr(repair_service, "validate_diagram", fake_validate)
    result = await dispatch_repair(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id="semantic:improper_termination",
                severity=Severity.ERROR,
                message="Rejected claims end as reimbursed.",
                tier=ValidationTier.TIER3,
                element_id="end_1",
            ),
            ValidationIssue(
                rule_id="semantic:inconsistent_naming",
                severity=Severity.WARNING,
                message="The decision label contradicts its outcomes.",
                tier=ValidationTier.TIER3,
                element_id="task_1",
            ),
        ],
        config=ExperimentConfig(max_repair_iters=5),
        atomic_repair_fn=fake_atomic_repair,
        single_plan=True,
    )

    assert assigned == [["semantic:improper_termination"]]
    assert context == [["semantic:inconsistent_naming"]]
    assert validation_calls == 1
    assert result.iterations == 1
    assert result.remaining_issues == [newly_discovered]
    assert result.stop_reason == StopReason.PROPOSAL_READY
    assert result.converged is False


async def test_dispatch_repair_uses_one_llm_call_when_batched_plan_converges():
    assigned: list[list[str]] = []

    async def fake_atomic_repair(diagram, issues, **kwargs):
        assigned.append([issue.rule_id for issue in issues])
        return [RenameNodeOp(id="task_1", new_name="Fixed in one plan")]

    result = await dispatch_repair(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id=f"R99{index}",
                severity=Severity.ERROR,
                message=f"Synthetic issue {index}.",
                element_id="task_1",
            )
            for index in range(3)
        ],
        atomic_repair_fn=fake_atomic_repair,
    )

    assert assigned == [["R990", "R991", "R992"]]
    assert result.iterations == 1
    assert result.converged is True
    assert len(result.applied_ops) == 1


async def test_dispatch_repair_regen_mode_uses_full_ir_replacement():
    calls = []

    async def fake_repair(diagram, issues, **kwargs):
        calls.append((diagram, issues, kwargs))
        return RepairResult(repaired_diagram=_minimal_valid_diagram())

    result = await dispatch_repair(
        _diagram_without_start(),
        issues=[
            ValidationIssue(
                rule_id="R001",
                severity=Severity.ERROR,
                message="Process has no start event.",
            )
        ],
        config=ExperimentConfig.model_validate({"repair_mode": "regen"}),
        repair_fn=fake_repair,
    )

    assert result.iterations == 1
    assert result.converged is True
    assert [op.op for op in result.applied_ops] == ["replace_diagram"]
    assert result.applied_op_origins == [OpOrigin.MODEL_REGEN]
    assert calls[0][1][0].rule_id == "R001"
    assert calls[0][2]["snapshot"] is False


async def test_dispatch_repair_respects_max_iteration_cap():
    calls = []

    async def fake_repair(diagram, issues, **kwargs):
        calls.append(issues[0].rule_id)
        # each round must leave a different diagram, or the no-progress detector
        # stops the loop before the cap
        updated = diagram.model_copy(deep=True)
        updated.processes[0].flow_nodes[0].name = f"renamed_{len(calls)}"
        return RepairResult(repaired_diagram=updated)

    result = await dispatch_repair(
        _diagram_without_start(),
        issues=[
            ValidationIssue(
                # a rule id with no registered quick-fix, so it routes to the LLM
                rule_id="R999",
                severity=Severity.ERROR,
                message="Process 'proc_1' has no start event.",
                element_id=None,
            )
        ],
        config=ExperimentConfig.model_validate(
            {"repair_mode": "regen", "max_repair_iters": 2}
        ),
        repair_fn=fake_repair,
    )

    assert result.iterations == 2
    assert result.converged is False
    assert calls[0] == "R999"
    assert len(calls) == 2
    assert result.remaining_issues
    assert [op.op for op in result.applied_ops] == [
        "replace_diagram",
        "replace_diagram",
    ]


async def test_dispatch_repair_targets_a_warning_once_no_error_remains():
    """Regression: the loop used to select errors only and never reach warnings."""
    calls = []

    async def fake_atomic_repair(diagram, issues, **kwargs):
        calls.append(issues[0].rule_id)
        return []

    result = await dispatch_repair(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id="R999",
                severity=Severity.WARNING,
                message="Gateway has fewer than 2 outgoing flows.",
            )
        ],
        atomic_repair_fn=fake_atomic_repair,
    )

    assert calls == ["R999"]
    assert result.iterations == 1


async def test_dispatch_repair_does_not_iterate_when_nothing_is_repairable():
    """A checker failure is a fact about the run, not a defect to repair."""
    calls = []

    async def fake_repair(diagram, issues, **kwargs):
        calls.append(issues)
        return RepairResult(repaired_diagram=diagram)

    result = await dispatch_repair(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id="LLM_PARSE_ERROR",
                severity=Severity.WARNING,
                message="LLM semantic validation returned an unparseable response.",
            )
        ],
        repair_fn=fake_repair,
    )

    assert result.iterations == 0
    assert result.converged is True
    assert result.remaining_issues[0].rule_id == "LLM_PARSE_ERROR"
    assert calls == []


async def test_repair_with_edit_ops_parses_atomic_llm_output(monkeypatch):
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return _atomic_response(
            [
                {
                    "op": "rename_node",
                    "id": "task_1",
                    "new_name": "Renamed task",
                }
            ]
        )

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    ops = await repair_with_edit_ops(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id="R999",
                severity=Severity.ERROR,
                message="Synthetic atomic repair issue.",
                element_id="task_1",
            )
        ],
        config=ExperimentConfig(),
    )

    assert [op.op for op in ops] == ["rename_node"]
    assert "Choose the operation by what is missing" in captured["system"]
    assert "Never use `add_node` to represent a missing sequence flow" in captured[
        "system"
    ]
    assert "replace_diagram" not in captured["system"]
    assert '"$defs"' not in captured["system"]
    assert json.loads(captured["prompt"])["repair_mode"] == "atomic"


async def test_repair_with_edit_ops_sends_diagram_specific_id_enums(monkeypatch):
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return _atomic_response([])

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    await repair_with_edit_ops(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id="R999",
                severity=Severity.ERROR,
                message="Synthetic atomic repair issue.",
            )
        ],
        config=ExperimentConfig(),
    )

    schema_defs = captured["schema"]["$defs"]
    assert schema_defs["RemoveFlowOp"]["properties"]["id"]["enum"] == ["sf_1", "sf_2"]
    assert schema_defs["RemoveNodeOp"]["properties"]["id"]["enum"] == [
        "end_1",
        "start_1",
        "task_1",
    ]
    # add_flow endpoints stay open strings so a node added earlier in the same
    # plan can be referenced; _validate_atomic_op_ids enforces the constraint
    source_ref = schema_defs["AddFlowOp"]["properties"]["source_ref"]
    assert schema_defs["AddFlowOp"]["properties"]["process_id"]["enum"] == ["proc_1"]
    assert "enum" not in source_ref
    assert "end_1" in source_ref["description"]
    assert "add_node" in source_ref["description"]
    payload = json.loads(captured["prompt"])
    assert payload["id_constraints"]["flow_ids"] == ["sf_1", "sf_2"]
    assert payload["id_constraints"]["gateway_ids"] == []
    # the schema goes out via response_format, so it must not be duplicated in the prose prompt
    assert '"sf_1"' not in captured["system"]
    assert len(captured["system"]) < 7_000
    ops_schema = schema_defs["AtomicEditOpsResult"]["properties"]["ops"]
    op_choices = ops_schema["items"]["anyOf"]
    assert op_choices[0] == {"$ref": "#/$defs/AddFlowOp"}
    assert op_choices[1] == {"$ref": "#/$defs/ChangeGatewayTypeOp"}
    assert {"$ref": "#/$defs/AddNodeOp"} in op_choices
    assert ops_schema["maxItems"] == 32


async def test_repair_with_edit_ops_rejects_flow_op_targeting_node_id(monkeypatch):
    async def fake_complete_structured(**kwargs):
        return _atomic_response([{"op": "remove_flow", "id": "task_1"}])

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    with pytest.raises(ValueError, match="expected an existing flow ID"):
        await repair_with_edit_ops(
            _minimal_valid_diagram(),
            issues=[
                ValidationIssue(
                    rule_id="R999",
                    severity=Severity.ERROR,
                    message="Synthetic atomic repair issue.",
                )
            ],
            config=ExperimentConfig(),
        )


async def test_repair_with_edit_ops_compacts_formal_issue_in_payload(monkeypatch):
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return _atomic_response([])

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    issue_with_witness = ValidationIssue(
        rule_id="woflan:soundness",
        severity=Severity.ERROR,
        message="Process is not sound.",
        tier=ValidationTier.TIER2,
        element_refs=["task_1"],
        source="woflan",
        formal_witness=FormalWitness(
            kind="soundness",
            description="task_1 is a dead task — it can never be reached.",
            counterexample_traces=[
                [
                    TraceStep(step=1, fired="start_1"),
                    TraceStep(step=2, fired="task_1"),
                ]
            ],
            dead_elements=["task_1"],
            uncovered_elements=["flow_1"],
        ),
        raw={"diagnostic_messages": ["Petri-net implementation detail."]},
    )

    await repair_with_edit_ops(
        _minimal_valid_diagram(),
        issues=[issue_with_witness],
        config=ExperimentConfig(),
    )

    payload = json.loads(captured["prompt"])
    assert payload["issues"] == [
        {
            "rule_id": "woflan:soundness",
            "severity": "error",
            "message": "Process is not sound.",
            "tier": "tier2",
            "source": "woflan",
            "affected_elements": ["task_1"],
            "formal_evidence": {
                "kind": "soundness",
                "dead_elements": ["task_1"],
                "uncovered_elements": ["flow_1"],
                "counterexample_traces": [["start_1", "task_1"]],
            },
        }
    ]
    assert "tier2_findings" not in payload


async def test_repair_diagram_compacts_formal_issue_in_payload(monkeypatch):
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return {
            "description": "Regenerated diagram.",
            "result": {
                "ir": _minimal_valid_diagram().model_dump_json(),
                "unresolved": [],
            },
        }

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    issue_with_witness = ValidationIssue(
        rule_id="woflan:soundness",
        severity=Severity.ERROR,
        message="Process is not sound.",
        tier=ValidationTier.TIER2,
        source="woflan",
        formal_witness=FormalWitness(
            kind="deadlock",
            description="No enabled transition at marking {p2: 1}.",
        ),
        raw={"sound": False},
    )

    from app.services.repair import repair_diagram

    await repair_diagram(
        _minimal_valid_diagram(),
        issues=[issue_with_witness],
        config=ExperimentConfig(),
        snapshot=False,
    )

    payload = json.loads(captured["prompt"])
    assert payload["issues"] == [
        {
            "rule_id": "woflan:soundness",
            "severity": "error",
            "message": "Process is not sound.",
            "tier": "tier2",
            "source": "woflan",
            "formal_evidence": {"kind": "deadlock"},
        }
    ]
    assert "tier2_findings" not in payload


async def test_structural_issue_payload_uses_affected_elements(monkeypatch):
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return _atomic_response([])

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    await repair_with_edit_ops(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id="R001",
                severity=Severity.ERROR,
                message="Process has no start event.",
                tier=ValidationTier.TIER1,
                element_id="process_1",
                source="rules",
                suggestion="Add a start event.",
            )
        ],
        config=ExperimentConfig(),
    )

    payload = json.loads(captured["prompt"])
    assert payload["issues"] == [
        {
            "rule_id": "R001",
            "severity": "error",
            "message": "Process has no start event.",
            "tier": "tier1",
            "source": "rules",
            "affected_elements": ["process_1"],
        }
    ]
    assert "tier2_findings" not in payload


async def test_dispatch_repair_re_validates_with_t2_between_iterations(monkeypatch):
    """Tier 2 findings from the re-validation step land in remaining_issues."""
    validate_calls = []
    original_validate = repair_service.validate_diagram

    async def fake_validate(diagram, **kwargs):
        validate_calls.append(kwargs)
        return await original_validate(diagram, **kwargs)

    monkeypatch.setattr(repair_service, "validate_diagram", fake_validate)

    async def fake_atomic_repair(diagram, issues, **kwargs):
        return [RenameNodeOp(id="task_1", new_name="Fixed")]

    config = ExperimentConfig.model_validate(
        {"tiers_enabled": {"t1": True, "t2": True, "t3": False}}
    )
    await dispatch_repair(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id="R999",
                severity=Severity.ERROR,
                message="Synthetic issue.",
                element_id="task_1",
            )
        ],
        config=config,
        atomic_repair_fn=fake_atomic_repair,
    )

    assert len(validate_calls) >= 1
    called_config = validate_calls[0].get("config")
    assert called_config is not None
    assert called_config.tiers_enabled.t2 is True


async def test_dispatch_repair_re_validates_with_t3_when_enabled(monkeypatch):
    """Without this the loop cannot re-report a semantic issue and always converges."""
    seen: list[bool] = []

    async def fake_validate(diagram, **kwargs):
        seen.append(kwargs.get("include_semantic"))
        return ValidationResult(is_valid=True, issues=[], semantic_issues=[])

    monkeypatch.setattr(repair_service, "validate_diagram", fake_validate)

    async def fake_atomic_repair(diagram, issues, **kwargs):
        return [RenameNodeOp(id="task_1", new_name="Fixed")]

    issue = ValidationIssue(
        rule_id="R999",
        severity=Severity.ERROR,
        message="Synthetic issue.",
        element_id="task_1",
    )
    for t3, expected in ((True, True), (False, False)):
        seen.clear()
        config = ExperimentConfig.model_validate(
            {"tiers_enabled": {"t1": True, "t2": False, "t3": t3}}
        )
        await dispatch_repair(
            _minimal_valid_diagram(),
            issues=[issue],
            config=config,
            atomic_repair_fn=fake_atomic_repair,
        )
        assert seen == [expected]


async def test_dispatch_repair_passes_other_issues_as_context(monkeypatch):
    """Warnings are not repaired, but the repairer still gets told about them."""
    captured: dict[str, Any] = {}

    async def fake_validate(diagram, **kwargs):
        return ValidationResult(is_valid=True, issues=[], semantic_issues=[])

    monkeypatch.setattr(repair_service, "validate_diagram", fake_validate)

    async def fake_atomic_repair(diagram, issues, **kwargs):
        captured.update(kwargs)
        return [RenameNodeOp(id="task_1", new_name="Fixed")]

    target = ValidationIssue(
        rule_id="R999",
        severity=Severity.ERROR,
        message="Synthetic issue.",
        element_id="task_1",
    )
    warning = ValidationIssue(
        rule_id="S001",
        severity=Severity.WARNING,
        message="Shared end event contradicts the rejection path.",
    )

    await dispatch_repair(
        _minimal_valid_diagram(),
        issues=[target, warning],
        config=ExperimentConfig(),
        atomic_repair_fn=fake_atomic_repair,
    )

    context = captured.get("context_issues")
    assert context is not None
    assert [i.rule_id for i in context] == ["S001"]


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
        processes=[
            BpmnProcess(
                id="proc_1", flow_nodes=[start, task, end], sequence_flows=flows
            )
        ],
    )


def _diagram_without_start() -> BpmnDiagram:
    task = FlowNode(id="task_1", type=FlowNodeType.TASK, outgoing=["sf_1"])
    end = FlowNode(id="end_1", type=FlowNodeType.END_EVENT, incoming=["sf_1"])
    flow = SequenceFlow(id="sf_1", source_ref="task_1", target_ref="end_1")
    return BpmnDiagram(
        definitions_id="def_1",
        processes=[
            BpmnProcess(id="proc_1", flow_nodes=[task, end], sequence_flows=[flow])
        ],
    )


async def test_atomic_repair_can_connect_a_node_it_just_added(monkeypatch):
    """Inserting a gateway needs add_node followed by add_flow onto it."""
    async def fake_complete_structured(**kwargs):
        return _atomic_response(
            [
                {
                    "op": "add_node",
                    "id": "gw_outcome",
                    "node_type": "exclusiveGateway",
                    "process_id": "proc_1",
                    "name": "Delivered?",
                },
                {"op": "remove_flow", "id": "sf_2"},
                {
                    "op": "add_flow",
                    "process_id": "proc_1",
                    "id": "sf_to_gw",
                    "source_ref": "task_1",
                    "target_ref": "gw_outcome",
                },
                {
                    "op": "add_flow",
                    "process_id": "proc_1",
                    "id": "sf_from_gw",
                    "source_ref": "gw_outcome",
                    "target_ref": "end_1",
                },
            ]
        )

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    ops = await repair_with_edit_ops(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id="S001",
                severity=Severity.ERROR,
                message="Uncontrolled split with contradictory outcomes.",
            )
        ],
        config=ExperimentConfig(),
    )

    assert [op.op for op in ops] == ["add_node", "remove_flow", "add_flow", "add_flow"]
    assert ops[3].source_ref == "gw_outcome"


async def test_atomic_repair_discards_disconnected_tasks_and_reprompts(monkeypatch):
    """Regression: rejected speculative tasks must not leak into the proposal."""
    responses = [
        {
            "ops": [
                {
                    "op": "add_node",
                    "id": "sf_card_feed",
                    "node_type": "task",
                    "process_id": "Process_expense_reimbursement",
                    "name": None,
                },
                {
                    "op": "add_node",
                    "id": "sf_audit_escalated",
                    "node_type": "task",
                    "process_id": "Process_expense_reimbursement",
                    "name": None,
                },
                {
                    "op": "change_gateway_type",
                    "id": "gw_close",
                    "new_type": "exclusiveGateway",
                },
            ]
        },
        {
            "ops": [
                {
                    "op": "add_node",
                    "id": "flow_card_feed_to_check",
                    "node_type": "task",
                    "process_id": "Process_expense_reimbursement",
                    "name": None,
                },
                {
                    "op": "add_node",
                    "id": "flow_audit_to_escalated",
                    "node_type": "task",
                    "process_id": "Process_expense_reimbursement",
                    "name": None,
                },
            ]
        },
        {
            "ops": [
                {
                    "op": "add_flow",
                    "process_id": "Process_expense_reimbursement",
                    "id": "sf_start_card_feed_to_check",
                    "source_ref": "start_card_feed",
                    "target_ref": "task_check",
                    "name": None,
                    "condition_expression": None,
                },
                {
                    "op": "add_flow",
                    "process_id": "Process_expense_reimbursement",
                    "id": "sf_task_audit_to_escalated",
                    "source_ref": "task_audit",
                    "target_ref": "end_escalated",
                    "name": None,
                    "condition_expression": None,
                },
            ]
        },
    ]
    prompts: list[dict[str, Any]] = []
    schemas: list[dict[str, Any]] = []

    async def fake_complete_structured(**kwargs):
        prompts.append(json.loads(kwargs["prompt"]))
        schemas.append(kwargs["schema"])
        return {
            "description": "Corrected repair plan.",
            "result": responses[len(prompts) - 1],
        }

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )
    diagram = PydanticConverter().parse(
        Path("data/test_cases/03_expense_reimbursement.bpmn").read_bytes()
    )

    ops = await repair_with_edit_ops(
        diagram,
        issues=[
            ValidationIssue(
                rule_id="R003",
                severity=Severity.ERROR,
                message="Start event has no outgoing sequence flow.",
                element_id="start_card_feed",
            )
        ],
        config=ExperimentConfig(),
    )

    assert len(prompts) == 3
    assert "repair_feedback" not in prompts[0]
    assert "Disconnected new node(s): sf_audit_escalated, sf_card_feed" in prompts[
        1
    ]["repair_feedback"]
    assert "Disconnected new node(s)" in prompts[2]["repair_feedback"]
    assert "do not merely invent another node ID" in prompts[1]["repair_feedback"]
    assert "Do not repeat" not in prompts[1]["repair_feedback"]
    assert [op.op for op in ops] == ["add_flow", "add_flow"]
    assert not any(op.op == "add_node" for op in ops)
    gateway_ids = schemas[0]["$defs"]["ChangeGatewayTypeOp"]["properties"]["id"][
        "enum"
    ]
    assert gateway_ids == ["gw_amount", "gw_close", "gw_decision", "gw_receipts"]
    assert "task_check" not in gateway_ids


async def test_atomic_repair_caps_runaway_disconnected_node_diagnostics(monkeypatch):
    calls = 0
    runaway_plan = {
        "ops": [
            {
                "op": "add_node",
                "id": f"sf_alternative_{index:02d}",
                "node_type": "task",
                "process_id": "proc_1",
                "name": None,
            }
            for index in range(40)
        ]
    }

    async def fake_complete_structured(**kwargs):
        nonlocal calls
        calls += 1
        return {"description": "Runaway plan.", "result": runaway_plan}

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    with pytest.raises(ValueError) as exc_info:
        await repair_with_edit_ops(
            _minimal_valid_diagram(),
            issues=[
                ValidationIssue(
                    rule_id="R999",
                    severity=Severity.ERROR,
                    message="Synthetic connectivity issue.",
                )
            ],
            config=ExperimentConfig(),
        )

    message = str(exc_info.value)
    assert calls == 3
    assert "sf_alternative_00" in message
    assert "sf_alternative_07" in message
    assert "sf_alternative_08" not in message
    assert "(and 32 more)" in message
    assert len(message) < 1_000


async def test_atomic_repair_still_rejects_an_id_that_is_never_created(monkeypatch):
    """Relaxing the enum must not reopen the hallucinated-id hole."""
    async def fake_complete_structured(**kwargs):
        return _atomic_response(
            [
                {
                    "op": "add_flow",
                    "process_id": "proc_1",
                    "id": "sf_new",
                    "source_ref": "task_1",
                    "target_ref": "gw_never_added",
                }
            ]
        )

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    with pytest.raises(ValueError, match="gw_never_added"):
        await repair_with_edit_ops(
            _minimal_valid_diagram(),
            issues=[
                ValidationIssue(
                    rule_id="R999",
                    severity=Severity.ERROR,
                    message="Synthetic atomic repair issue.",
                )
            ],
            config=ExperimentConfig(),
        )


async def test_atomic_repair_rejects_add_node_onto_an_existing_id(monkeypatch):
    """Seen live on R004: the model re-added the orphan instead of removing it."""
    async def fake_complete_structured(**kwargs):
        return _atomic_response(
            [
                {
                    "op": "add_node",
                    "id": "task_1",
                    "node_type": "task",
                    "process_id": "proc_1",
                }
            ]
        )

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    with pytest.raises(ValueError, match="already exists"):
        await repair_with_edit_ops(
            _minimal_valid_diagram(),
            issues=[
                ValidationIssue(
                    rule_id="R999",
                    severity=Severity.ERROR,
                    message="Synthetic atomic repair issue.",
                )
            ],
            config=ExperimentConfig(),
        )


async def test_atomic_repair_rejects_reference_to_a_node_removed_earlier(monkeypatch):
    """The id set shrinks as well as grows."""
    async def fake_complete_structured(**kwargs):
        return _atomic_response(
            [
                {"op": "remove_node", "id": "task_1", "cascade": False},
                {
                    "op": "add_flow",
                    "process_id": "proc_1",
                    "id": "sf_new",
                    "source_ref": "task_1",
                    "target_ref": "end_1",
                },
            ]
        )

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    with pytest.raises(ValueError, match="task_1"):
        await repair_with_edit_ops(
            _minimal_valid_diagram(),
            issues=[
                ValidationIssue(
                    rule_id="R999",
                    severity=Severity.ERROR,
                    message="Synthetic atomic repair issue.",
                )
            ],
            config=ExperimentConfig(),
        )
