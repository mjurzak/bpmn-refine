"""Stable semantic finding ids, repair-loop stopping, and honest repair results."""

from __future__ import annotations

import json

import pytest
from app.experiments import ExperimentConfig, RepairMode
from app.model.schema import (
    BpmnDiagram,
    BpmnProcess,
    FlowNode,
    FlowNodeType,
    SequenceFlow,
)
from app.repair.ops import AddNodeOp, RemoveNodeOp
from app.services import validation as validation_service
from app.services.repair import (
    OpOrigin,
    RepairResult,
    StopReason,
    _is_repairable,
    dispatch_repair,
)
from app.services.validation import (
    _SEMANTIC_SCHEMA,
    SemanticBasis,
    SemanticFinding,
    _normalise_semantic_findings,
    _sort_issues_by_severity,
    validate_diagram,
)
from app.validation.rules import (
    SemanticCategory,
    Severity,
    ValidationIssue,
    ValidationTier,
    semantic_rule_id,
)


def _diagram() -> BpmnDiagram:
    return BpmnDiagram(
        definitions_id="defs_1",
        processes=[
            BpmnProcess(
                id="Process_1",
                flow_nodes=[
                    FlowNode(id="start_1", type=FlowNodeType.START_EVENT),
                    FlowNode(id="task_1", type=FlowNodeType.TASK, name="Do it"),
                    FlowNode(id="end_1", type=FlowNodeType.END_EVENT),
                ],
                sequence_flows=[
                    SequenceFlow(id="sf_1", source_ref="start_1", target_ref="task_1"),
                    SequenceFlow(id="sf_2", source_ref="task_1", target_ref="end_1"),
                ],
            )
        ],
    )


def _finding(**overrides) -> SemanticFinding:
    data = {
        "category": SemanticCategory.MISSING_STEP,
        "classification_basis": SemanticBasis.REQUIRED_ACTIVITY_ABSENT,
        "severity": Severity.WARNING,
        "message": "No approval step before payment.",
        "element_refs": ["task_1"],
        "suggestion": "Add an approval task.",
    }
    data.update(overrides)
    return SemanticFinding(**data)


def test_validation_findings_put_errors_first_and_keep_stable_order():
    issues = [
        ValidationIssue(rule_id="warning:first", severity=Severity.WARNING, message="m"),
        ValidationIssue(rule_id="error:first", severity=Severity.ERROR, message="m"),
        ValidationIssue(rule_id="warning:second", severity=Severity.WARNING, message="m"),
        ValidationIssue(rule_id="error:second", severity=Severity.ERROR, message="m"),
    ]

    ordered = _sort_issues_by_severity(issues)

    assert [issue.rule_id for issue in ordered] == [
        "error:first",
        "error:second",
        "warning:first",
        "warning:second",
    ]
    assert [issue.rule_id for issue in issues] == [
        "warning:first",
        "error:first",
        "warning:second",
        "error:second",
    ]


# --------------------------------------------------------------------------
# a closed vocabulary and an enforced schema
# --------------------------------------------------------------------------


def test_semantic_rule_ids_are_derived_from_the_category():
    """The same defect must carry the same id on every run."""
    issues = _normalise_semantic_findings([_finding()], _diagram(), [])
    assert issues[0].rule_id == "semantic:missing_step"
    assert issues[0].rule_id == semantic_rule_id(SemanticCategory.MISSING_STEP)


def test_unwanted_action_is_a_closed_stable_semantic_category():
    issues = _normalise_semantic_findings(
        [_finding(category=SemanticCategory.UNWANTED_ACTION)], _diagram(), []
    )
    assert issues[0].rule_id == "semantic:unwanted_action"
    assert semantic_rule_id(SemanticCategory.UNWANTED_ACTION) == issues[0].rule_id


def test_the_response_schema_closes_the_category_set():
    categories = _SEMANTIC_SCHEMA["$defs"]["SemanticCategory"]
    assert set(categories["enum"]) == {
        category.value for category in SemanticCategory
    }
    category_field = _SEMANTIC_SCHEMA["$defs"]["SemanticFinding"]["properties"][
        "category"
    ]
    assert category_field == {"$ref": "#/$defs/SemanticCategory"}
    # no free-text escape hatch that would reintroduce per-run identifiers
    assert categories["type"] == "string"
    bases = _SEMANTIC_SCHEMA["$defs"]["SemanticBasis"]
    assert set(bases["enum"]) == {basis.value for basis in SemanticBasis}


def test_the_response_schema_is_strict():
    findings = _SEMANTIC_SCHEMA["$defs"]["SemanticFinding"]
    assert findings["additionalProperties"] is False
    assert _SEMANTIC_SCHEMA["additionalProperties"] is False
    assert _SEMANTIC_SCHEMA["required"] == ["description", "result"]
    assert "reference_evidence" in findings["required"]
    assert "classification_basis" in findings["required"]


@pytest.mark.asyncio
async def test_semantic_validation_uses_the_structured_call(monkeypatch):
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return {
            "description": "One semantic issue found.",
            "result": {"findings": [_finding().model_dump(mode="json")]},
        }

    monkeypatch.setattr(
        validation_service.llm_client, "complete_structured", fake_complete_structured
    )

    result = await validate_diagram(_diagram(), include_semantic=True)

    assert captured["schema"] == _SEMANTIC_SCHEMA
    assert result.semantic_issues[0].rule_id == "semantic:missing_step"
    assert result.semantic_issues[0].tier == ValidationTier.TIER3


@pytest.mark.asyncio
async def test_semantic_payload_numbers_reference_and_adds_geometry_free_view(
    monkeypatch,
):
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return {"description": "No issue.", "result": {"findings": []}}

    monkeypatch.setattr(
        validation_service.llm_client, "complete_structured", fake_complete_structured
    )

    await validate_diagram(
        _diagram(),
        include_semantic=True,
        config=ExperimentConfig(include_semantic_projection=True),
        reference_description="First step.\n\nSecond step.",
    )

    payload = json.loads(captured["prompt"])
    assert payload["reference_description"] == "L1: First step.\nL2: Second step."
    view = payload["semantic_view"]["processes"][0]
    assert view["nodes"][1] == {
        "id": "task_1",
        "type": "task",
        "name": "Do it",
        "incoming": ["sf_1"],
        "outgoing": ["sf_2"],
    }
    assert "bounds" not in json.dumps(view)


@pytest.mark.asyncio
async def test_a_malformed_semantic_reply_becomes_a_finding(monkeypatch):
    """A reply that does not fit the schema is a defect in the reply."""

    async def fake_complete_structured(**kwargs):
        return {
            "description": "Malformed finding.",
            "result": {"findings": [{"category": "not_a_category"}]},
        }

    monkeypatch.setattr(
        validation_service.llm_client, "complete_structured", fake_complete_structured
    )

    result = await validate_diagram(_diagram(), include_semantic=True)

    assert [issue.rule_id for issue in result.semantic_issues] == ["LLM_PARSE_ERROR"]


@pytest.mark.asyncio
async def test_a_provider_failure_is_raised_rather_than_attached_to_the_diagram(
    monkeypatch,
):
    """An outage is a fact about the run, not about the process being validated."""

    async def failing_complete_structured(**kwargs):
        raise ConnectionError("401 authentication_error")

    monkeypatch.setattr(
        validation_service.llm_client,
        "complete_structured",
        failing_complete_structured,
    )

    with pytest.raises(ConnectionError, match="authentication_error"):
        await validate_diagram(_diagram(), include_semantic=True)


# --------------------------------------------------------------------------
# references, severity, duplicates
# --------------------------------------------------------------------------


def test_unknown_element_references_are_dropped():
    """An invented id would send the user, and the next repair prompt, nowhere."""
    issues = _normalise_semantic_findings(
        [_finding(element_refs=["task_1", "ghost_task"])], _diagram(), []
    )
    assert issues[0].element_refs == ["task_1"]


def test_a_finding_with_only_unknown_references_becomes_process_wide():
    issues = _normalise_semantic_findings(
        [_finding(element_refs=["ghost_task"])], _diagram(), []
    )
    assert issues[0].element_refs == []
    assert issues[0].element_id is None


def test_info_severity_is_rejected_by_the_structured_contract():
    """An informational thought must be omitted, not upgraded into a false warning."""
    with pytest.raises(ValueError, match="error|warning"):
        _finding(severity=Severity.INFO)


def test_error_severity_is_preserved():
    issues = _normalise_semantic_findings(
        [_finding(severity=Severity.ERROR)], _diagram(), []
    )
    assert issues[0].severity == Severity.ERROR


def test_repeated_findings_in_one_response_are_collapsed():
    issues = _normalise_semantic_findings([_finding(), _finding()], _diagram(), [])
    assert len(issues) == 1


def test_a_structural_restatement_of_an_earlier_tier_is_dropped():
    """Tier 3 is asked not to restate tiers 1 and 2; this enforces it."""
    existing = [
        ValidationIssue(
            rule_id="R007",
            severity=Severity.ERROR,
            message="Node is unreachable.",
            tier=ValidationTier.TIER1,
            element_id="task_1",
        )
    ]
    issues = _normalise_semantic_findings(
        [_finding(category=SemanticCategory.UNREACHABLE_BRANCH)], _diagram(), existing
    )
    assert issues == []


def test_a_genuine_semantic_finding_on_a_flagged_element_survives():
    """Only structural categories count as restatements of an earlier tier."""
    existing = [
        ValidationIssue(
            rule_id="R007",
            severity=Severity.ERROR,
            message="Node is unreachable.",
            tier=ValidationTier.TIER1,
            element_id="task_1",
        )
    ]
    issues = _normalise_semantic_findings(
        [_finding(category=SemanticCategory.MISSING_STEP)], _diagram(), existing
    )
    assert len(issues) == 1


def test_semantic_findings_are_always_attributed_to_the_model():
    issues = _normalise_semantic_findings([_finding()], _diagram(), [])
    assert issues[0].source == "llm"
    assert issues[0].tier == ValidationTier.TIER3


def test_reference_evidence_is_preserved_for_posthoc_audit():
    finding = _finding()
    finding.reference_evidence = ["L2", "L4"]

    issues = _normalise_semantic_findings([finding], _diagram(), [])

    assert issues[0].raw == {
        "reference_evidence": ["L2", "L4"],
        "classification_basis": "required_activity_absent",
    }


def test_reference_evidence_gate_drops_unsupported_findings():
    finding = _finding(reference_evidence=[])

    issues = _normalise_semantic_findings(
        [finding],
        _diagram(),
        [],
        valid_reference_evidence={"L1"},
    )

    assert issues == []


def test_reference_evidence_gate_keeps_only_valid_line_labels():
    finding = _finding(reference_evidence=["L2", "L99"])

    issues = _normalise_semantic_findings(
        [finding],
        _diagram(),
        [],
        valid_reference_evidence={"L1", "L2"},
    )

    assert issues[0].raw == {
        "reference_evidence": ["L2"],
        "classification_basis": "required_activity_absent",
    }


# --------------------------------------------------------------------------
# the repair loop stops when it stops progressing
# --------------------------------------------------------------------------


def _unfixable_diagram() -> BpmnDiagram:
    """No start and no end event, so R001/R002 keep firing."""
    return BpmnDiagram(
        definitions_id="defs_1",
        processes=[
            BpmnProcess(
                id="Process_1",
                flow_nodes=[FlowNode(id="task_1", type=FlowNodeType.TASK)],
            )
        ],
    )


@pytest.mark.asyncio
async def test_an_iteration_that_changes_nothing_stops_the_loop():
    calls = []

    async def fake_repair(diagram, issues, **kwargs):
        calls.append(issues[0].rule_id)
        return RepairResult(repaired_diagram=diagram)

    result = await dispatch_repair(
        _unfixable_diagram(),
        issues=[
            ValidationIssue(
                rule_id="R999", severity=Severity.ERROR, message="Synthetic."
            )
        ],
        config=ExperimentConfig(repair_mode=RepairMode.REGEN, max_repair_iters=10),
        repair_fn=fake_repair,
    )

    assert result.iterations == 1
    assert len(calls) == 1
    assert result.stop_reason == StopReason.NO_PROGRESS
    assert result.converged is False


@pytest.mark.asyncio
async def test_a_repeated_state_stops_the_loop():
    """An edit that undoes the previous one is a cycle, not progress."""
    states = []

    async def fake_repair(diagram, issues, **kwargs):
        updated = diagram.model_copy(deep=True)
        # flip the name back and forth between two values
        node = updated.processes[0].flow_nodes[0]
        node.name = "B" if node.name == "A" else "A"
        states.append(node.name)
        return RepairResult(repaired_diagram=updated)

    start = _unfixable_diagram()
    start.processes[0].flow_nodes[0].name = "A"

    result = await dispatch_repair(
        start,
        issues=[
            ValidationIssue(
                rule_id="R999", severity=Severity.ERROR, message="Synthetic."
            )
        ],
        config=ExperimentConfig(repair_mode=RepairMode.REGEN, max_repair_iters=10),
        repair_fn=fake_repair,
    )

    assert result.stop_reason == StopReason.REPEATED_STATE
    assert result.iterations < 10


@pytest.mark.asyncio
async def test_a_productive_loop_still_runs_to_its_budget():
    """The detectors must not cut short a run that is genuinely changing."""
    counter = {"n": 0}

    async def fake_repair(diagram, issues, **kwargs):
        counter["n"] += 1
        updated = diagram.model_copy(deep=True)
        updated.processes[0].flow_nodes[0].name = f"step_{counter['n']}"
        return RepairResult(repaired_diagram=updated)

    result = await dispatch_repair(
        _unfixable_diagram(),
        issues=[
            ValidationIssue(
                rule_id="R999", severity=Severity.ERROR, message="Synthetic."
            )
        ],
        config=ExperimentConfig(repair_mode=RepairMode.REGEN, max_repair_iters=3),
        repair_fn=fake_repair,
    )

    assert result.iterations == 3
    assert result.stop_reason == StopReason.ITERATION_BUDGET


# --------------------------------------------------------------------------
# convergence, and failures that are not hidden
# --------------------------------------------------------------------------


def test_checker_failures_are_not_treated_as_repairable():
    assert _is_repairable(
        ValidationIssue(rule_id="R001", severity=Severity.ERROR, message="m")
    )
    assert not _is_repairable(
        ValidationIssue(rule_id="LLM_PARSE_ERROR", severity=Severity.WARNING, message="m")
    )
    assert not _is_repairable(
        ValidationIssue(
            rule_id="woflan:runtime_error", severity=Severity.WARNING, message="m"
        )
    )


@pytest.mark.asyncio
async def test_convergence_and_errors_resolved_are_reported_separately():
    """A run can drain every error and still leave a warning standing."""

    async def fake_atomic_repair(diagram, issues, **kwargs):
        return []

    result = await dispatch_repair(
        _diagram(),
        issues=[
            ValidationIssue(
                rule_id="semantic:missing_step",
                severity=Severity.WARNING,
                message="No approval step.",
                tier=ValidationTier.TIER3,
            )
        ],
        config=ExperimentConfig(max_repair_iters=1),
        atomic_repair_fn=fake_atomic_repair,
    )

    assert result.errors_resolved is True
    assert result.converged is True


@pytest.mark.asyncio
async def test_failed_operations_are_preserved_in_the_result():
    """A plan that only half applied must not read as a clean repair."""

    async def fake_atomic_repair(diagram, issues, **kwargs):
        return [
            AddNodeOp(
                id="task_new", node_type=FlowNodeType.TASK, process_id="Process_1"
            ),
            # task_1 still has incident flows and cascade is off, so this fails
            RemoveNodeOp(id="task_1", cascade=False),
        ]

    result = await dispatch_repair(
        _diagram(),
        issues=[
            ValidationIssue(
                rule_id="R999", severity=Severity.ERROR, message="Synthetic."
            )
        ],
        config=ExperimentConfig(max_repair_iters=1),
        atomic_repair_fn=fake_atomic_repair,
    )

    assert [op.op for op in result.applied_ops] == ["add_node"]
    assert len(result.failed_ops) == 1
    assert result.failed_ops[0].op.op == "remove_node"
    assert result.failed_ops[0].applied is False
    assert "incident flows" in (result.failed_ops[0].error or "")
    assert result.applied_op_origins == [OpOrigin.MODEL_PLAN]
    assert result.failed_op_origins == [OpOrigin.MODEL_PLAN]
