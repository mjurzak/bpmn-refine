"""Contract tests for the ablation controls used by the evaluation.

Each test pins the same property: flipping a control changes what the
application *executes*, not only what its configuration hash says. A control
that only reaches the run record is worse than an absent one, because the
record would then describe an experiment that never ran.
"""

from __future__ import annotations

import pytest

from app.experiments import ExperimentConfig, T2Tool, TiersEnabled, config_hash
from app.llm.client import _resolve_sampling
from app.llm.router import resolve_sampling
from app.model.schema import (
    BpmnDiagram,
    BpmnProcess,
    FlowNode,
    FlowNodeType,
    SequenceFlow,
)
from app.services import repair as repair_service
from app.services import validation as validation_service
from app.services.validation import validate_diagram
from app.validation.rules import (
    FormalWitness,
    Severity,
    TraceStep,
    ValidationIssue,
    ValidationTier,
    issue_to_dict,
)


def _broken_diagram() -> BpmnDiagram:
    """no start event and no end event, so tier 1 has something to report"""
    return BpmnDiagram(
        definitions_id="defs_1",
        processes=[
            BpmnProcess(
                id="Process_1",
                flow_nodes=[
                    FlowNode(id="task_1", type=FlowNodeType.TASK, name="Only task"),
                    FlowNode(id="task_2", type=FlowNodeType.TASK, name="Other task"),
                ],
                sequence_flows=[
                    SequenceFlow(id="sf_1", source_ref="task_1", target_ref="task_2")
                ],
            )
        ],
    )


def _woflan_issue() -> ValidationIssue:
    return ValidationIssue(
        rule_id="woflan:soundness",
        severity=Severity.ERROR,
        message="The process is not sound.",
        tier=ValidationTier.TIER2,
        element_refs=["task_1"],
        source="woflan",
        formal_witness=FormalWitness(
            kind="soundness",
            description="The process is not sound.",
            counterexample_traces=[[TraceStep(step=1, fired="task_1")]],
            dead_elements=["task_2"],
            uncovered_elements=["task_1"],
        ),
    )


# --------------------------------------------------------------------------
# tiers_enabled — all three switches must be live
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier1_can_be_disabled():
    """the control that used to be advertised but never read"""
    config = ExperimentConfig(tiers_enabled=TiersEnabled(t1=False, t2=False, t3=False))
    result = await validate_diagram(_broken_diagram(), config=config)
    assert result.issues == []


@pytest.mark.asyncio
async def test_tier1_runs_when_enabled():
    config = ExperimentConfig(tiers_enabled=TiersEnabled(t1=True, t2=False, t3=False))
    result = await validate_diagram(_broken_diagram(), config=config)
    assert {issue.rule_id for issue in result.issues} >= {"R001", "R002"}


@pytest.mark.asyncio
async def test_explicit_argument_overrides_the_configured_tier1():
    config = ExperimentConfig(tiers_enabled=TiersEnabled(t1=True))
    result = await validate_diagram(_broken_diagram(), include_t1=False, config=config)
    assert result.issues == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "t1,t2,t3",
    [
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, True, False),
        (True, False, True),
        (False, True, True),
        (True, True, True),
    ],
)
async def test_every_non_empty_tier_subset_executes_exactly_its_tiers(
    monkeypatch, t1, t2, t3
):
    """all seven subsets of {t1,t2,t3} must be reachable from the config alone"""
    executed: set[str] = set()

    async def fake_tier2(diagram, config):
        executed.add("t2")
        return []

    async def fake_tier3(diagram, existing_issues, config=None):
        executed.add("t3")
        return []

    monkeypatch.setattr(validation_service, "run_tier2_checkers", fake_tier2)
    monkeypatch.setattr(validation_service, "_semantic_validate", fake_tier3)

    def spy_validate(diagram):
        executed.add("t1")
        return validation_service.ValidationReport(issues=[])

    monkeypatch.setattr(validation_service, "validate", spy_validate)

    await validate_diagram(
        _broken_diagram(),
        config=ExperimentConfig(tiers_enabled=TiersEnabled(t1=t1, t2=t2, t3=t3)),
    )

    expected = {name for name, on in (("t1", t1), ("t2", t2), ("t3", t3)) if on}
    assert executed == expected


# --------------------------------------------------------------------------
# include_formal_evidence — the neuro-symbolic hinge
# --------------------------------------------------------------------------


def test_formal_evidence_is_present_by_default():
    payload = issue_to_dict(_woflan_issue())
    assert payload["formal_evidence"]["counterexample_traces"] == [["task_1"]]
    assert payload["formal_evidence"]["dead_elements"] == ["task_2"]


def test_formal_evidence_can_be_withheld():
    payload = issue_to_dict(_woflan_issue(), include_formal_evidence=False)
    assert "formal_evidence" not in payload


def test_withholding_evidence_keeps_the_verdict_and_the_localisation():
    """the ablation removes the witness, not the finding it belongs to

    If the off condition also dropped the verdict or the affected elements it
    would be measuring whether tier 2 ran at all, which is a different question.
    """
    with_evidence = issue_to_dict(_woflan_issue())
    without = issue_to_dict(_woflan_issue(), include_formal_evidence=False)

    assert without["rule_id"] == with_evidence["rule_id"]
    assert without["severity"] == with_evidence["severity"]
    assert without["message"] == with_evidence["message"]
    assert without["affected_elements"] == with_evidence["affected_elements"]
    assert without["tier"] == with_evidence["tier"]


@pytest.mark.asyncio
async def test_evidence_control_reaches_the_repair_prompt(monkeypatch):
    """the off condition must change the bytes sent to the model"""
    seen: list[str] = []

    async def fake_complete_structured(**kwargs):
        seen.append(kwargs["prompt"])
        return {"description": "No edits.", "result": {"ops": []}}

    monkeypatch.setattr(
        repair_service.llm_client, "complete_structured", fake_complete_structured
    )

    for include in (True, False):
        await repair_service.repair_with_edit_ops(
            _broken_diagram(),
            issues=[_woflan_issue()],
            config=ExperimentConfig(include_formal_evidence=include),
        )

    assert "counterexample_traces" in seen[0]
    assert "counterexample_traces" not in seen[1]
    # the checker verdict itself still travels in both conditions
    assert "not sound" in seen[0] and "not sound" in seen[1]


def test_evidence_control_changes_the_config_hash():
    on = ExperimentConfig(include_formal_evidence=True)
    off = ExperimentConfig(include_formal_evidence=False)
    assert config_hash(on) != config_hash(off)


# --------------------------------------------------------------------------
# temperature and seed — forwarded, or recorded as unsupported
# --------------------------------------------------------------------------


def test_configured_sampling_controls_reach_the_call():
    controls = resolve_sampling(ExperimentConfig(temperature=0.7, seed=42))
    assert controls == {"temperature": 0.7, "seed": 42}


def test_no_config_requests_no_sampling_controls():
    assert resolve_sampling(None) == {}


class _SeedlessProvider:
    supports_temperature = True
    supports_seed = False


class _FullProvider:
    supports_temperature = True
    supports_seed = True


def test_provider_that_supports_seed_receives_it():
    honored, unsupported = _resolve_sampling(_FullProvider(), 0.5, 42)
    assert honored == {"temperature": 0.5, "seed": 42}
    assert unsupported == []


def test_provider_without_seed_reports_it_rather_than_implying_it_ran():
    honored, unsupported = _resolve_sampling(_SeedlessProvider(), 0.5, 42)
    assert honored == {"temperature": 0.5}
    assert unsupported == ["seed"]


def test_unset_controls_are_neither_sent_nor_reported():
    honored, unsupported = _resolve_sampling(_SeedlessProvider(), None, None)
    assert honored == {}
    assert unsupported == []


@pytest.mark.asyncio
async def test_trace_records_the_controls_the_provider_could_not_honor(monkeypatch):
    from app.llm import client as llm_client
    from app.llm.tracing import get_traces, reset_trace_context, start_trace_context

    class _Adapter(_SeedlessProvider):
        async def complete(self, **kwargs):
            assert "seed" not in kwargs
            assert kwargs["temperature"] == 0.3
            return "ok"

    monkeypatch.setattr(llm_client, "get_provider", lambda name: _Adapter())

    token = start_trace_context()
    await llm_client.complete(prompt="p", temperature=0.3, seed=7)
    traces = get_traces()
    reset_trace_context(token)

    assert traces[0].temperature == 0.3
    assert traces[0].seed is None
    assert traces[0].unsupported_controls == ["seed"]


# --------------------------------------------------------------------------
# run records carry enough to reproduce the run
# --------------------------------------------------------------------------


def test_run_block_preserves_the_configuration_not_only_its_hash():
    from app.experiments import build_run_block

    config = ExperimentConfig(
        temperature=0.4, seed=11, t2_tools=[T2Tool.WOFLAN], max_repair_iters=3
    )
    run = build_run_block(
        config=config,
        model_used="test-model",
        converter="pydantic_ir@v1",
        rules_version="R001-R008",
    )

    assert run.config == config
    assert run.config_hash == config_hash(config)
    assert run.app_commit


def test_two_ablations_are_distinguishable_from_the_record_alone():
    from app.experiments import build_run_block

    def record(config: ExperimentConfig):
        return build_run_block(
            config=config,
            model_used="test-model",
            converter="pydantic_ir@v1",
            rules_version="R001-R008",
        )

    on = record(ExperimentConfig(include_formal_evidence=True))
    off = record(ExperimentConfig(include_formal_evidence=False))

    assert on.config_hash != off.config_hash
    assert on.config is not None and off.config is not None
    assert on.config.include_formal_evidence != off.config.include_formal_evidence
