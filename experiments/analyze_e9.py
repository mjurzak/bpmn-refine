"""Analyze the E9 three-run repeated-call stability diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Sequence

from experiments.analyze_e5 import _issue_scope, _issues
from experiments.analyze_results import (
    _Case,
    _anchor_pairs,
    _display_category,
    _expected_labels,
    _expected_localizations,
    _finding_value,
    _load_overlay,
    _load_truth,
    _matching_pairs,
    _overlay_record,
    _overlap_pairs,
    _predictions,
    _row_refs,
    normalize_finding,
)

OPERATORS = tuple(f"M{number:02d}" for number in range(1, 8))
RUNS_PER_CASE = 3
NEW_RUNS_PER_CASE = 2
EXPECTED_CONFIG_HASH = "90d893e3d439"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or not isinstance(
                value.get("input_path"), str
            ):
                raise ValueError(f"invalid record at {path}:{number}")
            records.append(value)
    return records


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config_hash(record: dict[str, Any]) -> str | None:
    run = record.get("run")
    return run.get("config_hash") if isinstance(run, dict) else None


def _validate_call(record: dict[str, Any]) -> dict[str, Any] | None:
    phases = record.get("phases")
    validate = phases.get("validate") if isinstance(phases, dict) else None
    calls = validate.get("calls") if isinstance(validate, dict) else None
    if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
        return None
    return calls[0]


def _first_e6_panel(
    records: Sequence[dict[str, Any]], dataset_root: Path
) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = []
    singles: dict[str, dict[str, Any]] = {}
    for record in records:
        truth = _load_truth(record["input_path"], dataset_root)
        if truth is None:
            if len(controls) < 3:
                controls.append(record)
            continue
        operators = truth.get("operators")
        if (
            truth.get("class") == "SEM"
            and truth.get("multiplicity") == 1
            and truth.get("interaction") == "single"
            and isinstance(operators, list)
            and len(operators) == 1
            and operators[0] in OPERATORS
            and operators[0] not in singles
        ):
            singles[operators[0]] = record
    if len(controls) != 3 or set(singles) != set(OPERATORS):
        raise ValueError(
            "E6 does not contain the required first three controls and M01-M07 singles"
        )
    return [*controls, *(singles[operator] for operator in OPERATORS)]


def _case(record: dict[str, Any], dataset_root: Path, overlay: dict[str, Any]) -> _Case:
    truth = _load_truth(record["input_path"], dataset_root)
    if truth is None:
        raise ValueError(f"positive case has no ground truth: {record['input_path']}")
    labels = _expected_labels(truth)
    localizations = _expected_localizations(
        truth, _overlay_record(overlay, record["input_path"])
    )
    return _Case(
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


def _semantic_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in _issues(record) if _issue_scope(row) == "semantic"]


def _category_multiset(record: dict[str, Any]) -> list[str]:
    categories = [
        _display_category(_finding_value(row)) for row in _semantic_rows(record)
    ]
    return sorted(category for category in categories if category)


def _semantic_refs(record: dict[str, Any]) -> set[str]:
    return set().union(*(_row_refs(row) for row in _semantic_rows(record)), set())


def _multiset_jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    left_counts, right_counts = Counter(left), Counter(right)
    union = sum((left_counts | right_counts).values())
    return sum((left_counts & right_counts).values()) / union if union else 1.0


def _set_jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _multiset_stability(values: Sequence[Sequence[str]]) -> dict[str, Any]:
    canonical = [tuple(value) for value in values]
    pairwise = [
        _multiset_jaccard(left, right) for left, right in combinations(values, 2)
    ]
    return {
        "runs": [list(value) for value in values],
        "distinct_multisets": len(set(canonical)),
        "all_three_equal": len(set(canonical)) == 1,
        "mean_pairwise_multiset_jaccard": sum(pairwise) / len(pairwise),
    }


def _set_stability(values: Sequence[set[str]]) -> dict[str, Any]:
    canonical = [tuple(sorted(value)) for value in values]
    pairwise = [_set_jaccard(left, right) for left, right in combinations(values, 2)]
    return {
        "runs": [list(value) for value in canonical],
        "distinct_sets": len(set(canonical)),
        "all_three_equal": len(set(canonical)) == 1,
        "mean_pairwise_jaccard": sum(pairwise) / len(pairwise),
    }


def _frequency_buckets(values: Sequence[int]) -> dict[str, int]:
    return {f"{count}/3": sum(value == count for value in values) for count in range(4)}


def _provider_versions(records: Sequence[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            call["provider_version"]
            for record in records
            for call in [_validate_call(record)]
            if call and isinstance(call.get("provider_version"), str)
        }
    )


def _unsupported_controls(records: Sequence[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            control
            for record in records
            for call in [_validate_call(record)]
            if call
            for control in call.get("unsupported_controls", [])
            if isinstance(control, str)
        }
    )


def _run_field_values(records: Sequence[dict[str, Any]], field: str) -> list[str]:
    return sorted(
        {
            value
            for record in records
            for run in [record.get("run")]
            if isinstance(run, dict)
            for value in [run.get(field)]
            if isinstance(value, str)
        }
    )


def _prompt_hashes(records: Sequence[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            prompt.get("hash")
            for record in records
            for run in [record.get("run")]
            if isinstance(run, dict)
            for prompt_versions in [run.get("prompt_versions")]
            if isinstance(prompt_versions, dict)
            for prompt in prompt_versions.values()
            if isinstance(prompt, dict) and isinstance(prompt.get("hash"), str)
        }
    )


def build_combined_records(
    e6_panel: Sequence[dict[str, Any]], new_records: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Wrap records without altering the reused E6 objects."""
    new_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in new_records:
        new_by_path[record["input_path"]].append(record)
    combined: list[dict[str, Any]] = []
    for e6_record in e6_panel:
        path = e6_record["input_path"]
        combined.append(
            {"input_path": path, "run_ordinal": 1, "source": "E6", "record": e6_record}
        )
        for ordinal, record in enumerate(
            sorted(new_by_path[path], key=lambda row: int(row.get("repeat", 0))),
            start=2,
        ):
            combined.append(
                {
                    "input_path": path,
                    "run_ordinal": ordinal,
                    "source": "E9-new-call",
                    "record": record,
                }
            )
    return combined


def analyze_e9(
    new_results_path: Path, e6_results_path: Path, dataset_root: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    new_records = _read_jsonl(new_results_path)
    e6_records = _read_jsonl(e6_results_path)
    e6_panel = _first_e6_panel(e6_records, dataset_root)
    expected_paths = [record["input_path"] for record in e6_panel]
    new_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in new_records:
        new_by_path[record["input_path"]].append(record)
    if set(new_by_path) != set(expected_paths):
        raise ValueError("new-call panel does not equal the first-matching E6 panel")
    if any(len(new_by_path[path]) != NEW_RUNS_PER_CASE for path in expected_paths):
        raise ValueError("each E9 panel case must have exactly two new calls")

    ordered_runs: dict[str, list[dict[str, Any]]] = {}
    for e6_record in e6_panel:
        path = e6_record["input_path"]
        additions = sorted(new_by_path[path], key=lambda row: int(row.get("repeat", 0)))
        ordered_runs[path] = [e6_record, *additions]
        if any(
            record.get("input_hash") != e6_record.get("input_hash")
            for record in additions
        ):
            raise ValueError(f"input hash drift for {path}")
        if any(
            record.get("description_hash") != e6_record.get("description_hash")
            for record in additions
        ):
            raise ValueError(f"description hash drift for {path}")
        if any(
            _config_hash(record) != EXPECTED_CONFIG_HASH
            for record in ordered_runs[path]
        ):
            raise ValueError(f"configuration drift for {path}")

    _, overlay, _ = _load_overlay(dataset_root, None)
    positive_rows: list[dict[str, Any]] = []
    for operator, e6_record in zip(OPERATORS, e6_panel[3:], strict=True):
        path = e6_record["input_path"]
        runs = ordered_runs[path]
        cases = [_case(record, dataset_root, overlay) for record in runs]
        category_detected = [
            bool(_matching_pairs(case.predicted, case.expected)) for case in cases
        ]
        anchor_detected = [bool(_anchor_pairs(case)) for case in cases]
        overlap_detected = [bool(_overlap_pairs(case)) for case in cases]
        expected_refs = set().union(
            *(refs for _, refs in cases[0].expected_localizations), set()
        )
        anchor_ref_runs = [
            set().union(
                *(
                    prediction.refs
                    for prediction in case.predicted
                    if prediction.refs & expected_refs
                ),
                set(),
            )
            for case in cases
        ]
        anchor_category_runs = [
            sorted(
                prediction.category
                for prediction in case.predicted
                if prediction.category and prediction.refs & expected_refs
            )
            for case in cases
        ]
        positive_rows.append(
            {
                "operator": operator,
                "input_path": path,
                "expected_category": sorted(cases[0].expected[0]),
                "expected_elements": sorted(expected_refs),
                "detection_frequency": {
                    "category": sum(category_detected),
                    "anchor": sum(anchor_detected),
                    "category_and_anchor": sum(overlap_detected),
                    "denominator": RUNS_PER_CASE,
                },
                "per_run_detection": [
                    {
                        "run_ordinal": index,
                        "category": category,
                        "anchor": anchor,
                        "category_and_anchor": overlap,
                    }
                    for index, (category, anchor, overlap) in enumerate(
                        zip(
                            category_detected,
                            anchor_detected,
                            overlap_detected,
                            strict=True,
                        ),
                        start=1,
                    )
                ],
                "category_stability": {
                    "all_semantic_findings": _multiset_stability(
                        [_category_multiset(record) for record in runs]
                    ),
                    "findings_overlapping_target_anchor": _multiset_stability(
                        anchor_category_runs
                    ),
                },
                "indicated_element_stability": {
                    "all_semantic_findings": _set_stability(
                        [_semantic_refs(record) for record in runs]
                    ),
                    "findings_overlapping_target_anchor": _set_stability(
                        anchor_ref_runs
                    ),
                },
            }
        )

    control_rows: list[dict[str, Any]] = []
    for e6_record in e6_panel[:3]:
        path = e6_record["input_path"]
        outcomes = []
        for ordinal, record in enumerate(ordered_runs[path], start=1):
            issues = _issues(record)
            scoped = Counter(_issue_scope(issue) for issue in issues)
            outcomes.append(
                {
                    "run_ordinal": ordinal,
                    "semantic_alerts": scoped["semantic"],
                    "structural_formal_alerts": scoped["structural_formal"],
                    "other_alerts": scoped["other"],
                    "total_alerts": len(issues),
                }
            )
        control_rows.append(
            {
                "input_path": path,
                "runs": outcomes,
                "alerted_runs": sum(row["total_alerts"] > 0 for row in outcomes),
                "semantic_alerts": sum(row["semantic_alerts"] for row in outcomes),
                "structural_formal_alerts": sum(
                    row["structural_formal_alerts"] for row in outcomes
                ),
                "other_alerts": sum(row["other_alerts"] for row in outcomes),
                "total_alerts": sum(row["total_alerts"] for row in outcomes),
            }
        )

    all_records = [record for path in expected_paths for record in ordered_runs[path]]
    new_calls = [_validate_call(record) for record in new_records]
    combined = build_combined_records(e6_panel, new_records)
    result = {
        "schema_version": "e9-repeatability-analysis-v1",
        "status": (
            "complete"
            if not any(record.get("error") for record in all_records)
            and all(call is not None for call in new_calls)
            and all(call and isinstance(call.get("output"), str) for call in new_calls)
            else "incomplete"
        ),
        "design": {
            "cases": 10,
            "semantic_cases": 7,
            "controls": 3,
            "runs_per_case": RUNS_PER_CASE,
            "reused_e6_records": 10,
            "new_independent_calls": 20,
            "selection_rule": "first three E6 controls, then first E6 single for each M01-M07 in record order",
            "seed_policy": "no seed requested; the final E6 configuration had no seed and Codex CLI does not support one",
        },
        "provenance": {
            "new_results_path": str(new_results_path),
            "new_results_sha256": _sha256(new_results_path),
            "e6_results_path": str(e6_results_path),
            "e6_results_sha256": _sha256(e6_results_path),
            "config_hash": EXPECTED_CONFIG_HASH,
            "application_commits": sorted(
                {
                    record.get("run", {}).get("app_commit")
                    for record in all_records
                    if isinstance(record.get("run"), dict)
                }
            ),
            "e6_provider_versions": _provider_versions(e6_panel),
            "new_provider_versions": _provider_versions(new_records),
            "provider_version_drift": (
                _provider_versions(e6_panel) != _provider_versions(new_records)
                if _provider_versions(new_records)
                else None
            ),
            "reused_e6_trial_ids": [record.get("trial_id") for record in e6_panel],
            "new_trial_ids": [record.get("trial_id") for record in new_records],
        },
        "integrity": {
            "records": len(all_records),
            "new_records": len(new_records),
            "errors": sum(bool(record.get("error")) for record in all_records),
            "new_provider_calls": sum(call is not None for call in new_calls),
            "new_raw_outputs_retained": sum(
                bool(call and isinstance(call.get("output"), str)) for call in new_calls
            ),
            "e6_raw_outputs_retained": sum(
                bool(
                    (call := _validate_call(record))
                    and isinstance(call.get("output"), str)
                )
                for record in e6_panel
            ),
            "models": _run_field_values(all_records, "model_used"),
            "converter_versions": _run_field_values(all_records, "converter"),
            "prompt_hashes": _prompt_hashes(all_records),
            "unsupported_controls_observed": _unsupported_controls(all_records),
            "unsupported_controls_declared": {
                "max_tokens": "requested by the validation client but not enforceable by Codex CLI",
                "temperature": "not supported by Codex CLI and not requested in E6 or E9",
                "seed": "not supported by Codex CLI and not requested in E6 or E9",
            },
        },
        "detection_frequency_buckets": {
            metric: _frequency_buckets(
                [row["detection_frequency"][metric] for row in positive_rows]
            )
            for metric in ("category", "anchor", "category_and_anchor")
        },
        "category_stability_summary": {
            scope: {
                "all_three_equal": sum(
                    row["category_stability"][scope]["all_three_equal"]
                    for row in positive_rows
                ),
                "cases": len(positive_rows),
                "mean_case_pairwise_multiset_jaccard": sum(
                    row["category_stability"][scope]["mean_pairwise_multiset_jaccard"]
                    for row in positive_rows
                )
                / len(positive_rows),
            }
            for scope in ("all_semantic_findings", "findings_overlapping_target_anchor")
        },
        "indicated_element_stability_summary": {
            scope: {
                "all_three_equal": sum(
                    row["indicated_element_stability"][scope]["all_three_equal"]
                    for row in positive_rows
                ),
                "cases": len(positive_rows),
                "mean_case_pairwise_jaccard": sum(
                    row["indicated_element_stability"][scope]["mean_pairwise_jaccard"]
                    for row in positive_rows
                )
                / len(positive_rows),
            }
            for scope in ("all_semantic_findings", "findings_overlapping_target_anchor")
        },
        "semantic_cases": positive_rows,
        "controls": {
            "cases": control_rows,
            "observations": 9,
            "alerted_observations": sum(row["alerted_runs"] for row in control_rows),
            "semantic_alerts": sum(row["semantic_alerts"] for row in control_rows),
            "structural_formal_alerts": sum(
                row["structural_formal_alerts"] for row in control_rows
            ),
            "other_alerts": sum(row["other_alerts"] for row in control_rows),
            "total_alerts": sum(row["total_alerts"] for row in control_rows),
        },
        "threats_to_validity": [
            "This is a ten-case descriptive diagnostic, not a new performance estimate.",
            "The first run is reused from E6 and predates the two new calls.",
            "The Codex CLI harness version changed between E6 and E9; model and application configuration remained fixed.",
            "No deterministic seed was available or invented, so variation includes uncontrolled provider sampling and serving effects.",
            "Raw prompts and provider outputs must remain retained for all three runs; derived combined records wrap rather than rewrite them.",
            "Source controls have no exhaustive semantic-negative annotation, so their semantic findings are control alerts rather than automatic false positives.",
        ],
    }
    return result, combined


def build_run_report(
    analysis: dict[str, Any],
    *,
    manifest_path: Path,
    spec_path: Path,
    analysis_path: Path,
    combined_path: Path,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    new_records = _read_jsonl(Path(analysis["provenance"]["new_results_path"]))
    usage_keys = (
        "calls",
        "calls_missing_usage",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
    )
    usage = {
        key: sum(int((record.get("usage") or {}).get(key, 0)) for record in new_records)
        for key in usage_keys
    }
    return {
        "schema_version": "e9-repeatability-run-v1",
        "status": analysis["status"],
        "experiment_id": manifest.get("experiment_id"),
        "dataset_version": manifest.get("dataset_version"),
        "authorization_scope": "user approved sending the fixed ten BPMN diagrams and matching English descriptions through Codex CLI for exactly twenty calls",
        "design": analysis["design"],
        "configuration": {
            "base": manifest.get("spec", {}).get("base"),
            "config_hashes": manifest.get("config_hashes"),
            "application_commit": manifest.get("app_commit"),
            "concurrency": manifest.get("concurrency"),
        },
        "execution": {
            "started_at": manifest.get("started_at"),
            "finished_at": manifest.get("finished_at"),
            "planned": manifest.get("trial_count"),
            "completed": manifest.get("completed"),
            "failed": manifest.get("failed"),
            "new_usage": usage,
        },
        "provider_harness_version_drift": {
            "e6": analysis["provenance"]["e6_provider_versions"],
            "e9": analysis["provenance"]["new_provider_versions"],
            "changed": analysis["provenance"]["provider_version_drift"],
            "interpretation": "the model and frozen application configuration match E6, but the CLI harness is not version-identical",
        },
        "integrity": analysis["integrity"],
        "headline": {
            "detection_frequency_buckets": analysis["detection_frequency_buckets"],
            "category_stability_summary": analysis["category_stability_summary"],
            "indicated_element_stability_summary": analysis[
                "indicated_element_stability_summary"
            ],
            "controls": {
                key: analysis["controls"][key]
                for key in (
                    "observations",
                    "alerted_observations",
                    "semantic_alerts",
                    "structural_formal_alerts",
                    "other_alerts",
                    "total_alerts",
                )
            },
        },
        "artifacts": {
            "spec": {"path": str(spec_path), "sha256": _sha256(spec_path)},
            "runner_manifest": {
                "path": str(manifest_path),
                "sha256": _sha256(manifest_path),
            },
            "new_results": {
                "path": analysis["provenance"]["new_results_path"],
                "sha256": analysis["provenance"]["new_results_sha256"],
            },
            "reused_e6_results": {
                "path": analysis["provenance"]["e6_results_path"],
                "sha256": analysis["provenance"]["e6_results_sha256"],
            },
            "analysis": {"path": str(analysis_path), "sha256": _sha256(analysis_path)},
            "combined_records": {
                "path": str(combined_path),
                "sha256": _sha256(combined_path),
            },
        },
        "threats_to_validity": analysis["threats_to_validity"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("new_results", type=Path)
    parser.add_argument("e6_results", type=Path)
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--combined-output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--run-output", type=Path)
    args = parser.parse_args(argv)
    try:
        result, combined = analyze_e9(
            args.new_results, args.e6_results, args.dataset_root
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    if args.combined_output:
        args.combined_output.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False) + "\n" for record in combined
            ),
            encoding="utf-8",
        )
    if args.run_output:
        if not all((args.manifest, args.spec, args.output, args.combined_output)):
            parser.error(
                "--run-output requires --manifest, --spec, --output, and --combined-output"
            )
        run_report = build_run_report(
            result,
            manifest_path=args.manifest,
            spec_path=args.spec,
            analysis_path=args.output,
            combined_path=args.combined_output,
        )
        args.run_output.write_text(
            json.dumps(run_report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
