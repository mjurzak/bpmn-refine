"""Tests for stable findings and honest repair measurement.

Between them these cover what an evaluation script needs before it can compare
two runs: semantic findings that carry the same identifier for the same defect,
a repair loop that stops when it stops making progress, and a result that does
not present a half-applied plan as a clean one.
"""

from __future__ import annotations

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
    SemanticFinding,
    _normalise_semantic_findings,
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
        "severity": Severity.WARNING,
        "message": "No approval step before payment.",
        "element_refs": ["task_1"],
        "suggestion": "Add an approval task.",
    }
    data.update(overrides)
    return SemanticFinding(**data)


# --------------------------------------------------------------------------
# a closed vocabulary and an enforced schema
# --------------------------------------------------------------------------


def test_semantic_rule_ids_are_derived_from_the_category():
    """the same defect must carry the same id on every run"""
    issues = _normalise_semantic_findings([_finding()], _diagram(), [])
    assert issues[0].rule_id == "semantic:missing_step"
    assert issues[0].rule_id == semantic_rule_id(SemanticCategory.MISSING_STEP)


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


def test_the_response_schema_is_strict():
    findings = _SEMANTIC_SCHEMA["$defs"]["SemanticFinding"]
    assert findings["additionalProperties"] is False
    assert _SEMANTIC_SCHEMA["additionalProperties"] is False


@pytest.mark.asyncio
async def test_semantic_validation_uses_the_structured_call(monkeypatch):
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return {"findings": [_finding().model_dump(mode="json")]}

    monkeypatch.setattr(
        validation_service.llm_client, "complete_structured", fake_complete_structured
    )

    result = await validate_diagram(_diagram(), include_semantic=True)

    assert captured["schema"] == _SEMANTIC_SCHEMA
    assert result.semantic_issues[0].rule_id == "semantic:missing_step"
    assert result.semantic_issues[0].tier == ValidationTier.TIER3


@pytest.mark.asyncio
async def test_a_malformed_semantic_reply_becomes_a_finding(monkeypatch):
    """a reply that does not fit the schema is a defect in the reply"""

    async def fake_complete_structured(**kwargs):
        return {"findings": [{"category": "not_a_category"}]}

    monkeypatch.setattr(
        validation_service.llm_client, "complete_structured", fake_complete_structured
    )

    result = await validate_diagram(_diagram(), include_semantic=True)

    assert [issue.rule_id for issue in result.semantic_issues] == ["LLM_PARSE_ERROR"]


@pytest.mark.asyncio
async def test_a_provider_failure_is_raised_rather_than_attached_to_the_diagram(
    monkeypatch,
):
    """an outage is a fact about the run, not about the process being validated"""

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
    """an invented ID would send the user, and the next repair prompt, nowhere"""
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


def test_info_severity_is_normalised_to_warning():
    """the prompt defines `info` as "omit it"; a model that sends one anyway
    must not produce a third severity the evaluation has to account for"""
    issues = _normalise_semantic_findings(
        [_finding(severity=Severity.INFO)], _diagram(), []
    )
    assert issues[0].severity == Severity.WARNING


def test_error_severity_is_preserved():
    issues = _normalise_semantic_findings(
        [_finding(severity=Severity.ERROR)], _diagram(), []
    )
    assert issues[0].severity == Severity.ERROR


def test_repeated_findings_in_one_response_are_collapsed():
    issues = _normalise_semantic_findings([_finding(), _finding()], _diagram(), [])
    assert len(issues) == 1


def test_a_structural_restatement_of_an_earlier_tier_is_dropped():
    """tier 3 is asked not to restate tiers 1 and 2; this enforces it"""
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
    """only structural categories are treated as restatements

    A missing approval step is a real semantic observation even when tier 1 has
    separately noticed the same task is unreachable.
    """
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


# --------------------------------------------------------------------------
# the repair loop stops when it stops progressing
# --------------------------------------------------------------------------


def _unfixable_diagram() -> BpmnDiagram:
    """no start and no end event, so R001/R002 keep firing"""
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
    """an edit that undoes the previous one is a cycle, not progress"""
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
    """the detectors must not cut short a run that is genuinely changing"""
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
    """a run can drain every error and still leave a warning standing"""

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

    # the diagram is structurally clean, so tier 1 leaves nothing behind
    assert result.errors_resolved is True
    assert result.converged is True


@pytest.mark.asyncio
async def test_failed_operations_are_preserved_in_the_result():
    """a plan that half applied must not read as a clean repair

    `applied_ops` alone would show one successful edit and no sign that the model
    also proposed an inapplicable one, which is exactly what a minimality metric
    would then miscount.
    """

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
    # both halves of the plan came from the same model call, applied or not
    assert result.applied_op_origins == [OpOrigin.MODEL_PLAN]
    assert result.failed_op_origins == [OpOrigin.MODEL_PLAN]
