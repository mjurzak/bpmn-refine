"""Create the partial-label, grouped report for E6 detection results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from experiments.analyze_e5 import _issue_scope, _issues
from experiments.analyze_results import (
    _Case,
    _anchor_pairs,
    _expected_labels,
    _expected_localizations,
    _load_overlay,
    _load_truth,
    _matching_pairs,
    _operator_cases,
    _overlay_record,
    _overlap_pairs,
    _predictions,
    normalize_finding,
)


def _ratio(value: int, denominator: int) -> float | None:
    return value / denominator if denominator else None


def _load_cases(
    results_path: Path, dataset_root: Path
) -> tuple[list[_Case], list[dict[str, Any]], list[dict[str, Any]]]:
    _, overlay, _ = _load_overlay(dataset_root, None)
    cases: list[_Case] = []
    controls: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    with results_path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict) or not isinstance(
                record.get("input_path"), str
            ):
                raise ValueError(f"invalid record at {results_path}:{number}")
            records.append(record)
            truth = _load_truth(record["input_path"], dataset_root)
            if truth is None:
                controls.append(record)
                continue
            labels = _expected_labels(truth)
            localizations = _expected_localizations(
                truth, _overlay_record(overlay, record["input_path"])
            )
            cases.append(
                _Case(
                    record=record,
                    truth=truth,
                    expected=[normalize_finding(label) for label in labels],
                    expected_localizations=[
                        ([normalize_finding(label) for label in row_labels], refs)
                        for row_labels, refs in localizations
                    ],
                    predicted=_predictions(record),
                    scored=True,
                    case_id=record["input_path"],
                )
            )
    return cases, controls, records


def _summary(cases: Sequence[_Case]) -> dict[str, Any]:
    expected = sum(len(case.expected) for case in cases)
    category_matches = [
        len(_matching_pairs(case.predicted, case.expected)) for case in cases
    ]
    anchor_matches = [len(_anchor_pairs(case)) for case in cases]
    overlap_matches = [len(_overlap_pairs(case)) for case in cases]
    predictions = sum(len(case.predicted) for case in cases)
    count = len(cases)
    paired = [case for case in cases if len(case.expected) == 2]

    def paired_summary(selected: Sequence[_Case]) -> dict[str, Any]:
        matches = [
            len(_matching_pairs(case.predicted, case.expected)) for case in selected
        ]
        exact_set = [
            matched == len(case.expected) and len(case.predicted) == len(case.expected)
            for case, matched in zip(selected, matches, strict=True)
        ]
        return {
            "cases": len(selected),
            "any_detected": sum(value > 0 for value in matches),
            "any_detected_rate": _ratio(
                sum(value > 0 for value in matches), len(selected)
            ),
            "all_detected": sum(
                value == len(case.expected)
                for case, value in zip(selected, matches, strict=True)
            ),
            "all_detected_rate": _ratio(
                sum(
                    value == len(case.expected)
                    for case, value in zip(selected, matches, strict=True)
                ),
                len(selected),
            ),
            "exact_set_conservative": sum(exact_set),
            "exact_set_conservative_rate": _ratio(sum(exact_set), len(selected)),
        }

    return {
        "cases": count,
        "injected_targets": expected,
        "target_category": {
            "matched": sum(category_matches),
            "recall": _ratio(sum(category_matches), expected),
        },
        "target_anchor": {
            "matched": sum(anchor_matches),
            "recall": _ratio(sum(anchor_matches), expected),
        },
        "target_category_and_anchor": {
            "matched": sum(overlap_matches),
            "recall": _ratio(sum(overlap_matches), expected),
        },
        "case_detection": {
            "any_category": sum(value > 0 for value in category_matches),
            "any_category_rate": _ratio(
                sum(value > 0 for value in category_matches), count
            ),
            "all_categories": sum(
                value == len(case.expected)
                for case, value in zip(cases, category_matches, strict=True)
            ),
            "all_categories_rate": _ratio(
                sum(
                    value == len(case.expected)
                    for case, value in zip(cases, category_matches, strict=True)
                ),
                count,
            ),
        },
        "predicted_findings": predictions,
        "unmatched_findings_unadjudicated": predictions - sum(category_matches),
        "paired": paired_summary(paired),
    }


def _control_summary(controls: Sequence[dict[str, Any]]) -> dict[str, Any]:
    in_scope = []
    semantic = []
    other = []
    outcomes = []
    for record in controls:
        rows = _issues(record)
        scoped = [row for row in rows if _issue_scope(row) == "structural_formal"]
        semantic_rows = [row for row in rows if _issue_scope(row) == "semantic"]
        other_rows = [row for row in rows if _issue_scope(row) == "other"]
        in_scope.extend(scoped)
        semantic.extend(semantic_rows)
        other.extend(other_rows)
        outcomes.append(
            {
                "input_path": record["input_path"],
                "in_scope_alert": bool(scoped),
                "semantic_alert": bool(semantic_rows),
                "other_alert": bool(other_rows),
            }
        )
    count = len(controls)
    in_scope_cases = sum(row["in_scope_alert"] for row in outcomes)
    semantic_cases = sum(row["semantic_alert"] for row in outcomes)
    return {
        "cases": count,
        "in_scope_structural_formal": {
            "alerted_cases": in_scope_cases,
            "false_positive_rate": _ratio(in_scope_cases, count),
            "findings": len(in_scope),
        },
        "semantic_descriptive": {
            "alerted_cases": semantic_cases,
            "alert_rate": _ratio(semantic_cases, count),
            "findings": len(semantic),
        },
        "other_descriptive": {"findings": len(other)},
        "outcomes": outcomes,
    }


def analyze_e6(results_path: Path, dataset_root: Path) -> dict[str, Any]:
    cases, controls, records = _load_cases(results_path, dataset_root)
    by_class = {
        name: [case for case in cases if case.truth and case.truth.get("class") == name]
        for name in ("SEM", "STRUCT", "SOUND")
    }
    by_shape = {
        "single": [case for case in cases if len(case.expected) == 1],
        "disjoint": [
            case
            for case in cases
            if case.truth and case.truth.get("interaction") == "disjoint"
        ],
        "interacting": [
            case
            for case in cases
            if case.truth and case.truth.get("interaction") == "interacting"
        ],
    }
    operator_cases = _operator_cases(cases)
    durations = [
        float(record["duration_ms"])
        for record in records
        if isinstance(record.get("duration_ms"), (int, float))
    ]
    usage_keys = (
        "calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
    )
    usage = {
        key: sum(
            value
            for record in records
            for value in [(record.get("usage") or {}).get(key)]
            if isinstance(value, int)
        )
        for key in usage_keys
    }
    usage["calls_missing_usage"] = sum(
        int((record.get("usage") or {}).get("calls_missing_usage", 0))
        for record in records
    )
    parse_error_records = sum(
        any(issue.get("rule_id") == "LLM_PARSE_ERROR" for issue in _issues(record))
        for record in records
    )
    successful_contract_records = sum(
        not record.get("error")
        and not any(
            issue.get("rule_id") == "LLM_PARSE_ERROR" for issue in _issues(record)
        )
        for record in records
    )
    source_findings: dict[str, int] = {}
    for record in records:
        for issue in _issues(record):
            source = issue.get("source")
            key = source if isinstance(source, str) and source else "unknown"
            source_findings[key] = source_findings.get(key, 0) + 1
    return {
        "schema_version": "e6-analysis-v1",
        "results_path": str(results_path),
        "policy": {
            "targets": "positive_partial_labels_with_one_to_one_matching",
            "unmatched_findings": "unadjudicated_not_automatic_false_positives",
            "exact_set": "conservative_descriptive_only",
            "control_semantic_alerts": "descriptive_only",
        },
        "records": len(records),
        "positive_cases": len(cases),
        "overall": _summary(cases),
        "by_class": {name: _summary(group) for name, group in by_class.items()},
        "by_shape": {name: _summary(group) for name, group in by_shape.items()},
        "by_operator": {
            name: _summary(group) for name, group in sorted(operator_cases.items())
        },
        "controls": _control_summary(controls),
        "response_contract": {
            "schema": "holistic",
            "successful_records": successful_contract_records,
            "records": len(records),
            "success_rate": _ratio(successful_contract_records, len(records)),
            "parse_error_records": parse_error_records,
            "reference_evidence_supported": False,
            "classification_basis_supported": False,
            "note": "reference evidence and classification basis belong to the semantic schema, not the active holistic schema",
        },
        "source_findings": dict(sorted(source_findings.items())),
        "usage": usage,
        "latency_ms": {
            "mean": sum(durations) / len(durations) if durations else None,
            "total": sum(durations) if durations else None,
        },
        "errors": sum(bool(record.get("error")) for record in records),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = analyze_e6(args.results, args.dataset_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
