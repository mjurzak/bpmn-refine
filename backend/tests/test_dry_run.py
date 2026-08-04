"""Tests for the ablation dry run.

The dry run is the gate's exit condition, so it needs its own guard: a sweep
that quietly stopped exercising a control would still produce a full-looking
record and would still pass every other test in the suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.dry_run import DEFAULT_INPUTS, build_matrix, execute
from app.dry_run import MockProvider
from app.services.chat import _CHAT_SCHEMA
from app.services.repair import (
    _ATOMIC_OPS_SCHEMA,
    _RAW_XML_SCHEMA,
    _REGENERATION_SCHEMA,
)
from app.services.validation import _SEMANTIC_SCHEMA

# the fixture that fires tier 1 and tier 2 together
UNSOUND_INPUT = Path("data/test_cases/03_expense_reimbursement.bpmn")


@pytest.fixture(scope="module")
def record():
    import asyncio

    return asyncio.run(execute(list(DEFAULT_INPUTS)))


def _cases(record, prefix, input_path=UNSOUND_INPUT):
    return [
        case
        for case in record["cases"]
        if case["case"].startswith(prefix) and case["input"] == str(input_path)
    ]


def test_the_matrix_covers_every_control():
    prefixes = {case.split(":")[0] for case, _ in build_matrix()}
    assert prefixes == {
        "tiers",
        "ir_format",
        "repair_mode",
        "formal_evidence",
        "max_repair_iters",
        "sampling",
    }


@pytest.mark.asyncio
async def test_mock_provider_implements_every_structured_contract():
    provider = MockProvider()
    semantic_prompt = (
        '{"diagram":{"processes":[{"flow_nodes":[{"id":"task_1"}]}]}}'
    )
    cases = [
        (_SEMANTIC_SCHEMA, semantic_prompt, "findings"),
        (
            _ATOMIC_OPS_SCHEMA,
            '{"id_constraints":{"node_ids":["task_1"]}}',
            "ops",
        ),
        (_REGENERATION_SCHEMA, '{"diagram":{"definitions_id":"defs"}}', "ir"),
        (_RAW_XML_SCHEMA, "<definitions/>", "xml"),
    ]

    for schema, prompt, result_key in cases:
        response = await provider.complete_structured(
            prompt=prompt,
            system=None,
            model="mock",
            schema=schema,
        )
        parsed = json.loads(response.text)
        assert parsed["description"].strip()
        assert result_key in parsed["result"]

    chat = await provider.complete_structured_with_history(
        messages=[{"role": "user", "content": "hello"}],
        system=None,
        model="mock",
        schema=_CHAT_SCHEMA,
    )
    parsed_chat = json.loads(chat.text)
    assert parsed_chat["description"].strip()
    assert parsed_chat["result"]["diagram"] is None


def test_the_record_names_the_implementation_it_ran_against(record):
    """a configuration without a revision cannot be reproduced"""
    assert record["app_commit"]
    assert record["provider"].startswith("mocked")


def test_every_case_carries_its_configuration(record):
    for case in record["cases"]:
        assert case["config"]
        assert case["run"]["config_hash"]
        assert case["run"]["app_commit"]


def test_all_seven_tier_subsets_execute_exactly_their_tiers(record):
    observed = {}
    for case in _cases(record, "tiers:"):
        label = case["case"].split(":")[1]
        executed = case["executed"]
        observed[label] = (
            executed["tier1_ran"],
            executed["tier2_ran"],
            executed["tier3_ran"],
        )

    assert observed == {
        "t1": (True, False, False),
        "t2": (False, True, False),
        "t3": (False, False, True),
        "t1t2": (True, True, False),
        "t1t3": (True, False, True),
        "t2t3": (False, True, True),
        "t1t2t3": (True, True, True),
    }


def test_the_evidence_ablation_changes_the_prompt_but_not_the_checker(record):
    on, off = _cases(record, "formal_evidence:")
    assert on["case"].endswith("on") and off["case"].endswith("off")

    # the checker runs in both conditions — that is the point of the ablation
    assert on["executed"]["tier2_ran"] is True
    assert off["executed"]["tier2_ran"] is True
    # ...and only the witness in the prompt differs
    assert on["executed"]["formal_evidence_in_prompt"] is True
    assert off["executed"]["formal_evidence_in_prompt"] is False
    assert on["result"]["issue_ids"] == off["result"]["issue_ids"]


def test_repair_modes_take_different_paths(record):
    atomic, regen = _cases(record, "repair_mode:")
    assert atomic["executed"]["call_kinds"] == ["complete_structured"]
    assert regen["executed"]["call_kinds"] == ["complete_structured"]


def test_the_iteration_budget_is_honored(record):
    capped = next(
        case for case in _cases(record, "max_repair_iters:") if case["case"].endswith(":1")
    )
    assert capped["result"]["iterations"] == 1
    assert capped["result"]["stop_reason"] == "iteration_budget"


def test_each_iteration_budget_setting_produces_a_different_run(record):
    """the control has to separate its settings, not just its config hash

    A constant rename is a no-op from the second iteration on, so 3, 5, and 10
    all stopped at `repeated_state` after two — three settings, one observation.
    """
    by_budget = {
        int(case["case"].split(":")[1]): case["result"]
        for case in _cases(record, "max_repair_iters:")
    }

    for budget, result in by_budget.items():
        assert result["iterations"] == budget, f"budget {budget} stopped early"
        assert result["stop_reason"] == "iteration_budget"


def test_every_ir_format_is_exercised(record):
    formats = {case["case"].split(":")[1] for case in _cases(record, "ir_format:")}
    assert formats == {"pydantic", "pydantic_json", "yaml", "mermaid", "compact_json"}
    for case in _cases(record, "ir_format:"):
        assert case["executed"]["llm_calls"] > 0


def test_an_unsupported_sampling_control_is_recorded_as_such(record):
    requested = next(
        case for case in _cases(record, "sampling:") if "temperature" in case["case"]
    )
    executed = requested["executed"]
    # the mock provider declares no seed support, so the record must say so
    assert executed["sampling_unsupported"] == ["seed"]
    assert 0.2 in executed["sampling_honored"]["temperature"]


def test_the_quick_fix_path_makes_no_model_call(record):
    quick_fix_input = str(DEFAULT_INPUTS[0])
    case = next(
        case
        for case in record["cases"]
        if case["case"] == "repair_mode:atomic" and case["input"] == quick_fix_input
    )
    # R001 has a registered quick fix, so the repair never reaches a provider
    assert case["executed"]["llm_calls"] == 0
    assert case["result"]["applied_ops"]


@pytest.mark.asyncio
async def test_the_dry_run_is_deterministic():
    """two runs over the same inputs must agree on everything but the clock"""
    first = await execute([UNSOUND_INPUT])
    second = await execute([UNSOUND_INPUT])

    for left, right in zip(first["cases"], second["cases"], strict=True):
        assert left["case"] == right["case"]
        assert left["config"] == right["config"]
        assert left["executed"] == right["executed"]
        assert left["result"] == right["result"]
        assert left["run"]["config_hash"] == right["run"]["config_hash"]
