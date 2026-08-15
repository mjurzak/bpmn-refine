"""Focused, provider-free tests for experiments.analyze_results."""

from __future__ import annotations

import json
from pathlib import Path

from experiments.analyze_results import (
    analyze_results,
    ground_truth_path,
    normalize_finding,
)


def _record(input_path: str, issue_ids: list[str], *, tokens: int | None = 10, error=None):
    usage = {
        "input_tokens": tokens,
        "output_tokens": 0 if tokens is not None else None,
        "total_tokens": tokens,
        "calls_missing_usage": 0 if tokens is not None else 1,
    }
    return {
        "input_path": input_path,
        "duration_ms": 12,
        "error": error,
        "pre_validation": {
            "issue_ids": issue_ids,
            "issues": [
                {"rule_id": issue_id, "element_refs": ["Task_1"]}
                for issue_id in issue_ids
            ],
        },
        "usage": usage,
        "run": {
            "model_configured": "mock-model",
            "model_used": "mock-model",
            "config": {
                "provider_override": "codex_cli",
                "ir_format": "pydantic",
                "tiers_enabled": {"t1": False, "t2": False, "t3": True},
                "llm_validation_scope": "holistic",
            },
        },
        "phases": {"validate": {"calls": [{"provider": "codex_cli"}]}},
    }


def _write_truth(
    root: Path,
    relative: str,
    findings: list[str],
    elements=None,
    *,
    multiplicity: int = 1,
    interaction: str | None = None,
):
    path = root / "ground_truth" / Path(relative).with_suffix(".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "variant_id": Path(relative).with_suffix("").as_posix(),
                "expected_findings": findings,
                "expected_elements": elements or [],
                "multiplicity": multiplicity,
                "interaction": interaction,
            }
        ),
        encoding="utf-8",
    )


def test_ground_truth_mirror_and_holistic_aliases():
    root = Path("/tmp/eval")
    assert ground_truth_path("data/eval/v1.0/variants/single/S01/01.bpmn", root) == (
        root / "ground_truth/single/S01/01.json"
    )
    assert normalize_finding("llm:missing_start_event") == frozenset({"R001"})
    assert normalize_finding("llm:unreachable_region") == frozenset(
        {"R007", "dead transition"}
    )
    assert normalize_finding("semantic:missing_step") == frozenset({"missing_step"})


def test_analysis_scores_injected_and_clean_controls(tmp_path):
    _write_truth(tmp_path, "single/S01/01.bpmn", ["R001"], ["Task_1"])
    results = tmp_path / "results.jsonl"
    rows = [
        _record(
            str(tmp_path / "variants/single/S01/01.bpmn"),
            ["llm:missing_start_event"],
        ),
        _record(str(tmp_path / "seeds/01.bpmn"), ["llm:unwanted_action"]),
        _record(str(tmp_path / "seeds/02.bpmn"), []),
    ]
    for index, row in enumerate(rows, start=1):
        row["usage"]["cached_input_tokens"] = index
    results.write_text("\n".join(json.dumps(row) for row in rows) + "\nnot-json\n")

    output = analyze_results(results, tmp_path)
    assert output["records_read"] == 3
    assert output["malformed_lines"] == 1
    assert output["malformed_or_error_count"] == 1
    group = output["groups"][0]
    assert group["injected_case_detection"]["recall"] == 1.0
    assert group["finding"]["precision"] == 1.0
    assert group["finding"]["recall"] == 1.0
    assert group["clean_false_positive_rate"] == 0.5
    assert group["localization_overlap"] == 1.0
    assert group["tokens"] == 30
    assert group["usage"]["cached_input_tokens"] == 6


def test_analysis_keeps_ablation_arms_in_separate_groups(tmp_path):
    results = tmp_path / "results.jsonl"
    without_description = _record(str(tmp_path / "seeds/01.bpmn"), [])
    with_description = _record(str(tmp_path / "seeds/01.bpmn"), [])
    without_description["run"]["config"].update(
        {"include_reference_description": False, "reasoning_effort": "low"}
    )
    with_description["run"]["config"].update(
        {"include_reference_description": True, "reasoning_effort": "high"}
    )
    results.write_text(
        "\n".join(json.dumps(row) for row in (without_description, with_description))
        + "\n",
        encoding="utf-8",
    )

    groups = analyze_results(results, tmp_path)["groups"]
    assert len(groups) == 2
    assert {group["config"]["include_reference_description"] for group in groups} == {
        False,
        True,
    }
    assert {group["config"]["reasoning_effort"] for group in groups} == {
        "low",
        "high",
    }


def test_analysis_is_conservative_with_missing_usage_and_errors(tmp_path):
    _write_truth(tmp_path, "single/S02/01.bpmn", ["R002"])
    results = tmp_path / "results.jsonl"
    rows = [
        _record(
            str(tmp_path / "variants/single/S02/01.bpmn"),
            ["llm:missing_end_event"],
            tokens=None,
            error="mock failure after record",
        ),
        _record(str(tmp_path / "seeds/01.bpmn"), [], tokens=None),
    ]
    results.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    output = analyze_results(results, tmp_path)
    group = output["groups"][0]
    assert group["tokens"] is None
    assert group["usage"]["calls_missing_usage"] == 2
    assert group["usage"]["complete"] is False
    assert group["error_count"] == 1
    assert group["malformed_or_error_count"] == 1
    assert output["ranking"][0]["tokens"] is None


def test_unrelated_finding_does_not_count_as_injected_case_detection(tmp_path):
    _write_truth(tmp_path, "single/S03/01.bpmn", ["R001"])
    results = tmp_path / "results.jsonl"
    results.write_text(
        json.dumps(
            _record(
                str(tmp_path / "variants/single/S03/01.bpmn"),
                ["llm:missing_end_event"],
            )
        )
        + "\n",
        encoding="utf-8",
    )

    group = analyze_results(results, tmp_path)["groups"][0]
    assert group["injected_case_detection"]["true_positive"] == 0
    assert group["injected_case_detection"]["false_negative"] == 1
    assert group["injected_case_detection"]["recall"] == 0.0


def test_analysis_reports_paired_detection_without_double_counting(tmp_path):
    _write_truth(
        tmp_path,
        "disjoint/S01+S03/01.bpmn",
        ["R001", "R005"],
        multiplicity=2,
        interaction="disjoint",
    )
    results = tmp_path / "results.jsonl"
    results.write_text(
        json.dumps(
            _record(
                str(tmp_path / "variants/disjoint/S01+S03/01.bpmn"),
                ["llm:missing_start_event"],
            )
        )
        + "\n",
        encoding="utf-8",
    )

    paired = analyze_results(results, tmp_path)["groups"][0]["paired_detection"]
    assert paired["overall"] == {
        "cases": 1,
        "any_detected": 1.0,
        "all_detected": 0.0,
        "exact_set": 0.0,
    }
    assert paired["disjoint"] == paired["overall"]
    assert paired["interacting"]["cases"] == 0


def test_analysis_reports_partial_pair_repair(tmp_path):
    _write_truth(
        tmp_path,
        "disjoint/S01+S03/02.bpmn",
        ["R001", "R005"],
        multiplicity=2,
        interaction="disjoint",
    )
    record = _record(
        str(tmp_path / "variants/disjoint/S01+S03/02.bpmn"),
        ["R001", "R005"],
    )
    record["repair"] = {
        "applied_ops": ["add_node"],
        "failed_ops": [],
    }
    record["post_validation"] = {
        "is_valid": False,
        "issue_ids": ["R005"],
        "issues": [{"rule_id": "R005", "element_refs": ["Task_1"]}],
    }
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(record) + "\n", encoding="utf-8")

    repair = analyze_results(results, tmp_path)["groups"][0]["repair"]
    assert repair["application_success"] == 1.0
    assert repair["complete_target_removal"] == 0.0
    assert repair["one_resolved"] == 1.0
    assert repair["both_resolved"] == 0.0
