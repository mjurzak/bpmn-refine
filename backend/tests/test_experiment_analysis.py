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
    defects=None,
    operators=None,
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
                "defects": defects or [],
                "operators": operators or [],
            }
        ),
        encoding="utf-8",
    )


def _write_overlay(root: Path, records: dict[str, dict]):
    (root / "ground_truth_overlay.json").write_text(
        json.dumps({"schema_version": "test-v1", "cases": records}),
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
    _write_overlay(
        tmp_path,
        {
            "seeds/01": {"baseline": {"status": "clean", "human_verified": True}},
            "seeds/02": {"baseline": {"status": "clean", "human_verified": True}},
        },
    )
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
    assert group["comparable_total_tokens"] == 30
    assert group["usage"]["cached_input_tokens"] == 6


def test_analysis_adds_anthropic_cache_only_for_comparable_usage(tmp_path):
    results = tmp_path / "results.jsonl"
    claude = _record(str(tmp_path / "seeds/01.bpmn"), [], tokens=10)
    claude["run"]["config"]["provider_override"] = "claude_cli"
    claude["phases"]["validate"]["calls"][0]["provider"] = "claude_cli"
    claude["usage"]["cached_input_tokens"] = 7
    results.write_text(json.dumps(claude) + "\n", encoding="utf-8")

    group = analyze_results(results, tmp_path)["groups"][0]
    assert group["tokens"] == 10
    assert group["comparable_total_tokens"] == 17
    assert group["usage"]["comparable_total_tokens"] == 17


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
    assert output["ranking"][0]["comparable_total_tokens"] is None


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


def test_analysis_preserves_repeated_categories_and_matches_rows_by_identity(tmp_path):
    _write_truth(
        tmp_path,
        "single/M01/01.bpmn",
        ["repeated", "repeated"],
        defects=[
            {"operator": "M01", "expected_finding": "repeated", "expected_elements": ["A"]},
            {"operator": "M01", "expected_finding": "repeated", "expected_elements": ["B"]},
        ],
        operators=["M01"],
    )
    record = _record(str(tmp_path / "variants/single/M01/01.bpmn"), ["repeated", "repeated"])
    # The compact IDs are sorted/summary data and intentionally do not follow
    # the order of detailed provider rows.
    record["pre_validation"]["issues"] = [
        {"rule_id": "repeated", "element_refs": ["B"]},
        {"rule_id": "repeated", "element_refs": ["A"]},
    ]
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(record) + "\n", encoding="utf-8")

    group = analyze_results(results, tmp_path)["groups"][0]
    assert group["finding"]["predicted"] == 2
    assert group["finding"]["matched"] == 2
    assert group["strict_localization"]["matched"] == 2
    assert group["localization_overlap"] == 1.0
    assert group["operator_breakdown"]["M01"]["finding"]["matched"] == 2


def test_analysis_reports_category_only_strict_and_localization_coverage(tmp_path):
    _write_truth(
        tmp_path,
        "single/M02/01.bpmn",
        ["missing_step"],
        ["A", "B"],
        defects=[
            {
                "operator": "M02",
                "expected_finding": "missing_step",
                "expected_elements": ["A", "B"],
            }
        ],
        operators=["M02"],
    )
    record = _record(str(tmp_path / "variants/single/M02/01.bpmn"), ["semantic:missing_step"])
    record["pre_validation"]["issues"][0]["element_refs"] = ["A"]
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(record) + "\n", encoding="utf-8")

    group = analyze_results(results, tmp_path)["groups"][0]
    assert group["category_only"]["recall"] == 1.0
    assert group["strict_localization"]["matched"] == 0
    assert group["localization"]["expected_coverage"] == 1.0
    assert group["localization"]["predicted_coverage"] == 1.0
    assert group["localization"]["matched_coverage"] == 1.0


def test_analysis_exposes_clean_finding_counts_and_categories(tmp_path):
    _write_overlay(
        tmp_path,
        {
            "seeds/01": {"baseline": {"status": "clean", "human_verified": True}},
            "seeds/02": {"baseline": {"status": "clean", "human_verified": True}},
        },
    )
    results = tmp_path / "results.jsonl"
    first = _record(str(tmp_path / "seeds/01.bpmn"), ["semantic:missing_step"])
    second = _record(str(tmp_path / "seeds/02.bpmn"), ["semantic:missing_step", "semantic:unwanted_action"])
    second["pre_validation"]["issues"][1]["element_refs"] = []
    results.write_text(
        "\n".join(json.dumps(row) for row in (first, second)) + "\n",
        encoding="utf-8",
    )

    group = analyze_results(results, tmp_path)["groups"][0]
    assert group["clean"]["predicted_findings"] == 3
    assert group["clean"]["false_positive_cases"] == 2
    assert group["clean_finding_categories"] == {
        "missing_step": 2,
        "unwanted_action": 1,
    }
    assert group["clean"]["missing_localization"] == 1


def test_analysis_reports_reference_evidence_coverage(tmp_path):
    _write_truth(tmp_path, "single/M01/01.bpmn", ["missing_step"])
    record = _record(
        str(tmp_path / "variants/single/M01/01.bpmn"),
        ["semantic:missing_step", "semantic:unwanted_action"],
    )
    record["pre_validation"]["issues"][0]["raw"] = {
        "reference_evidence": ["L2", "L4"]
    }
    record["pre_validation"]["issues"][1]["raw"] = {
        "reference_evidence": []
    }
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(record) + "\n", encoding="utf-8")

    evidence = analyze_results(results, tmp_path)["groups"][0][
        "reference_evidence"
    ]
    assert evidence == {
        "predicted_findings": 2,
        "findings_with_evidence": 1,
        "findings_missing_evidence": 1,
        "coverage": 0.5,
        "line_citations": 2,
    }


def test_analysis_adds_deterministic_paired_macro_f1_bootstrap(tmp_path):
    _write_truth(tmp_path, "single/M01/01.bpmn", ["R001"])
    _write_truth(tmp_path, "single/M01/02.bpmn", ["R001"])
    rows = []
    for provider, model, issue_ids in (
        ("codex_cli", "gpt", ["R001"]),
        ("codex_cli", "gpt", []),
        ("claude_cli", "claude", []),
        ("claude_cli", "claude", []),
    ):
        path = tmp_path / ("variants/single/M01/01.bpmn" if len(rows) % 2 == 0 else "variants/single/M01/02.bpmn")
        row = _record(str(path), issue_ids)
        row["run"]["model_configured"] = model
        row["run"]["config"]["provider_override"] = provider
        row["phases"]["validate"]["calls"][0]["provider"] = provider
        rows.append(row)
    results = tmp_path / "results.jsonl"
    results.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    output = analyze_results(results, tmp_path)
    comparison = output["paired_macro_f1"][0]
    assert comparison["cases"] == 2
    assert comparison["bootstrap"]["replicates"] == 2000
    assert comparison == analyze_results(results, tmp_path)["paired_macro_f1"][0]


def test_seed_controls_require_verified_clean_overlay_and_suppress_ranking(tmp_path):
    _write_truth(tmp_path, "single/M01/01.bpmn", ["R001"])
    results = tmp_path / "results.jsonl"
    rows = [
        _record(str(tmp_path / "variants/single/M01/01.bpmn"), ["R001"]),
        _record(str(tmp_path / "seeds/18.bpmn"), ["semantic:noise"]),
    ]
    results.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    output = analyze_results(results, tmp_path)
    group = output["groups"][0]
    assert group["clean_cases"] == 0
    assert group["clean_false_positive_rate"] is None
    assert group["unadjudicated_controls"] == 1
    assert output["ranking"][0]["eligible"] is False
    assert "no_verified_clean_controls" in output["ranking"][0]["ineligibility_reasons"]
    assert output["winner"] is None


def test_overlay_auto_loads_schema_version_and_variant_localization_override(tmp_path):
    _write_truth(
        tmp_path,
        "single/M02/01.bpmn",
        ["missing_step"],
        ["WrongTruthRef"],
        defects=[
            {
                "operator": "M02",
                "expected_finding": "missing_step",
                "expected_elements": ["WrongTruthRef"],
            }
        ],
        operators=["M02"],
    )
    _write_overlay(
        tmp_path,
        {
            "single/M02/01": {
                "injected": [
                    {
                        "operator": "M02",
                        "categories": ["missing_step"],
                        "localization": {"allowed_variant_refs": ["AllowedRef"]},
                    }
                ]
            },
            "seeds/18": {
                "baseline": {"status": "clean", "human_verified": True}
            },
        },
    )
    record = _record(str(tmp_path / "variants/single/M02/01.bpmn"), ["missing_step"])
    record["pre_validation"]["issues"][0]["element_refs"] = ["AllowedRef"]
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(record) + "\n", encoding="utf-8")

    output = analyze_results(results, tmp_path)
    group = output["groups"][0]
    assert output["ground_truth_overlay"]["schema_version"] == "test-v1"
    assert output["ground_truth_overlay"]["records"] == 2
    assert group["exact_localization"]["matched"] == 1
    assert group["localization_detection"]["matched"] == 1


def test_relation_anchor_detection_is_independent_of_predicted_category(tmp_path):
    _write_truth(
        tmp_path,
        "single/M03/01.bpmn",
        ["expected_category"],
        ["Anchor"],
    )
    record = _record(str(tmp_path / "variants/single/M03/01.bpmn"), ["wrong_category"])
    record["pre_validation"]["issues"][0]["element_refs"] = ["Anchor"]
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(record) + "\n", encoding="utf-8")

    group = analyze_results(results, tmp_path)["groups"][0]
    assert group["category_only"]["matched"] == 0
    assert group["localization_detection"]["matched"] == 1
    assert group["category_localization"]["matched"] == 0
    assert group["category_accuracy_given_anchor"]["accuracy"] == 0.0
