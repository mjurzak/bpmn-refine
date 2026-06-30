import json

from app.experiments import ExperimentConfig
from app.model.schema import BpmnDiagram, BpmnProcess, FlowNode, FlowNodeType, SequenceFlow
from app.repair.ops import RenameNodeOp
from app.services import repair as repair_service
from app.services.repair import RepairResult, dispatch_repair, repair_with_edit_ops
from app.validation.rules import FormalWitness, Severity, ValidationIssue


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
    assert calls[0][1][0].rule_id == "R999"


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
    assert calls[0][1][0].rule_id == "R001"
    assert calls[0][2]["snapshot"] is False


async def test_dispatch_repair_respects_max_iteration_cap():
    calls = []

    async def fake_repair(diagram, issues, **kwargs):
        calls.append(issues[0].rule_id)
        return RepairResult(repaired_diagram=diagram)

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
    assert [op.op for op in result.applied_ops] == ["replace_diagram", "replace_diagram"]


async def test_dispatch_repair_does_not_iterate_when_no_errors_remain():
    calls = []

    async def fake_repair(diagram, issues, **kwargs):
        calls.append(issues)
        return RepairResult(repaired_diagram=diagram)

    result = await dispatch_repair(
        _minimal_valid_diagram(),
        issues=[
            ValidationIssue(
                rule_id="R999",
                severity=Severity.WARNING,
                message="Gateway has fewer than 2 outgoing flows.",
            )
        ],
        repair_fn=fake_repair,
    )

    assert result.iterations == 0
    assert result.converged is True
    assert result.remaining_issues[0].rule_id == "R999"
    assert calls == []


async def test_repair_with_edit_ops_parses_atomic_llm_output(monkeypatch):
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return {
            "ops": [
                {
                    "op": "rename_node",
                    "id": "task_1",
                    "new_name": "Renamed task",
                }
            ]
        }

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
    assert "Available atomic EditOps" in captured["system"]
    assert "replace_diagram" not in captured["system"]
    assert json.loads(captured["prompt"])["repair_mode"] == "atomic"


async def test_repair_with_edit_ops_includes_tier2_findings_in_payload(monkeypatch):
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return {"ops": []}

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    issue_with_witness = ValidationIssue(
        rule_id="woflan:soundness",
        severity=Severity.ERROR,
        message="Process is not sound.",
        element_refs=["task_1"],
        source="woflan",
        formal_witness=FormalWitness(
            kind="soundness",
            description="task_1 is a dead task — it can never be reached.",
        ),
    )

    await repair_with_edit_ops(
        _minimal_valid_diagram(),
        issues=[issue_with_witness],
        config=ExperimentConfig(),
    )

    payload = json.loads(captured["prompt"])
    assert "tier2_findings" in payload
    assert len(payload["tier2_findings"]) == 1
    finding = payload["tier2_findings"][0]
    assert finding["rule_id"] == "woflan:soundness"
    assert finding["formal_witness"]["kind"] == "soundness"
    assert "task_1" in finding["affected_elements"]


async def test_repair_diagram_includes_tier2_findings_in_payload(monkeypatch):
    captured = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        return json.dumps({"ir": _minimal_valid_diagram().model_dump(mode="json"), "unresolved": []})

    monkeypatch.setattr(repair_service.llm_client, "complete", fake_complete)

    issue_with_witness = ValidationIssue(
        rule_id="woflan:soundness",
        severity=Severity.ERROR,
        message="Process is not sound.",
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
    assert "tier2_findings" in payload
    finding = payload["tier2_findings"][0]
    assert finding["formal_witness"]["kind"] == "deadlock"


async def test_issues_without_formal_witness_omit_tier2_findings(monkeypatch):
    captured = {}

    async def fake_complete_structured(**kwargs):
        captured.update(kwargs)
        return {"ops": []}

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
            )
        ],
        config=ExperimentConfig(),
    )

    payload = json.loads(captured["prompt"])
    assert "tier2_findings" not in payload


async def test_dispatch_repair_re_validates_with_t2_between_iterations(monkeypatch):
    """tier 2 findings from the re-validation step are included in remaining_issues"""
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

    # validate_diagram must have been called with a config that has t2 enabled
    assert len(validate_calls) >= 1
    called_config = validate_calls[0].get("config")
    assert called_config is not None
    assert called_config.tiers_enabled.t2 is True


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


def _diagram_without_start() -> BpmnDiagram:
    task = FlowNode(id="task_1", type=FlowNodeType.TASK, outgoing=["sf_1"])
    end = FlowNode(id="end_1", type=FlowNodeType.END_EVENT, incoming=["sf_1"])
    flow = SequenceFlow(id="sf_1", source_ref="task_1", target_ref="end_1")
    return BpmnDiagram(
        definitions_id="def_1",
        processes=[BpmnProcess(id="proc_1", flow_nodes=[task, end], sequence_flows=[flow])],
    )

