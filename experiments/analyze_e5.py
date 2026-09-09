"""Analyze the paired E5 validator-ablation panels."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

from experiments.analyze_results import (
    _expected_labels,
    _finding_value,
    _row_refs,
    _split_findings,
    ground_truth_path,
    normalize_finding,
)


_SEMANTIC_CATEGORIES = frozenset(
    {
        "missing_step",
        "contradictory_flow",
        "unreachable_branch",
        "missing_exception_handling",
        "inconsistent_naming",
        "improper_termination",
        "unwanted_action",
    }
)
_IN_SCOPE_ALIASES = frozenset(
    {
        "R001",
        "R002",
        "R005",
        "R006",
        "R007",
        "dead transition",
        "deadlock",
        "lack of synchronisation",
        "improper completion",
    }
)


def _pipeline(record: dict[str, Any]) -> str:
    run = record.get("run")
    config = run.get("config") if isinstance(run, dict) else None
    tiers = config.get("tiers_enabled") if isinstance(config, dict) else None
    key = (
        bool(tiers.get("t1")) if isinstance(tiers, dict) else False,
        bool(tiers.get("t2")) if isinstance(tiers, dict) else False,
        bool(tiers.get("t3")) if isinstance(tiers, dict) else False,
    )
    names = {
        (True, True, False): "deterministic",
        (False, False, True): "llm_only",
        (True, True, True): "full",
    }
    if key not in names:
        raise ValueError(f"unsupported E5 tier configuration: {key}")
    return names[key]


def _truth(input_path: str, dataset_root: Path) -> dict[str, Any] | None:
    path = ground_truth_path(input_path, dataset_root)
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _operator(truth: dict[str, Any] | None) -> str | None:
    if not truth:
        return None
    defects = truth.get("defects")
    if isinstance(defects, list):
        for defect in defects:
            if isinstance(defect, dict) and isinstance(defect.get("operator"), str):
                return defect["operator"]
    operators = truth.get("operators")
    if isinstance(operators, list) and operators and isinstance(operators[0], str):
        return operators[0]
    return None


def _expected_refs(truth: dict[str, Any] | None) -> set[str]:
    if not truth:
        return set()
    refs: set[str] = set()
    defects = truth.get("defects")
    if isinstance(defects, list):
        for defect in defects:
            if not isinstance(defect, dict):
                continue
            values = defect.get("expected_elements", [])
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                refs.update(
                    value for value in values if isinstance(value, str) and value
                )
    values = truth.get("expected_elements", [])
    if isinstance(values, str):
        values = [values]
    if isinstance(values, list):
        refs.update(value for value in values if isinstance(value, str) and value)
    return refs


def _issues(record: dict[str, Any]) -> list[dict[str, Any]]:
    validation = record.get("pre_validation")
    rows = validation.get("issues") if isinstance(validation, dict) else None
    return (
        [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    )


def _source(row: dict[str, Any]) -> str:
    value = row.get("source")
    finding = _finding_value(row)
    if value == "llm" or (
        isinstance(finding, str) and finding.lower().startswith("llm:")
    ):
        return "llm"
    return "deterministic"


def _issue_aliases(row: dict[str, Any]) -> frozenset[str]:
    aliases: set[str] = set()
    for label in _split_findings(_finding_value(row)):
        aliases.update(normalize_finding(label))
    return frozenset(aliases)


def _issue_scope(row: dict[str, Any]) -> str:
    value = _finding_value(row)
    aliases = _issue_aliases(row)
    if aliases.intersection(_IN_SCOPE_ALIASES):
        return "structural_formal"
    if isinstance(value, str) and value.lower().startswith("woflan:"):
        return "structural_formal"
    if aliases and aliases.issubset(_SEMANTIC_CATEGORIES):
        return "semantic"
    return "other"


def _read_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or not isinstance(
                value.get("input_path"), str
            ):
                raise ValueError(f"invalid result record at {path}:{number}")
            records.append(value)
    return records


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _summarize(records: Iterable[dict[str, Any]], dataset_root: Path) -> dict[str, Any]:
    by_pipeline: dict[str, list[dict[str, Any]]] = {
        "deterministic": [],
        "llm_only": [],
        "full": [],
    }
    for record in records:
        by_pipeline[_pipeline(record)].append(record)

    output: dict[str, Any] = {}
    outcome_maps: dict[str, dict[str, dict[str, Any]]] = {}
    for pipeline, rows in by_pipeline.items():
        positive_outcomes: list[dict[str, Any]] = []
        control_outcomes: list[dict[str, Any]] = []
        operator_counts: dict[str, Counter[str]] = {}
        tokens = Counter[str]()
        durations: list[float] = []
        errors = 0
        calls_missing_usage = 0

        for record in rows:
            input_path = record["input_path"]
            truth = _truth(input_path, dataset_root)
            issues = _issues(record)
            usage = record.get("usage")
            if isinstance(usage, dict):
                for key in (
                    "calls",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "cached_input_tokens",
                ):
                    value = usage.get(key)
                    if isinstance(value, int):
                        tokens[key] += value
                missing = usage.get("calls_missing_usage")
                if isinstance(missing, int):
                    calls_missing_usage += missing
            duration = record.get("duration_ms")
            if isinstance(duration, (int, float)):
                durations.append(float(duration))
            errors += bool(record.get("error"))

            if truth is None:
                scoped = [
                    row for row in issues if _issue_scope(row) == "structural_formal"
                ]
                semantic = [row for row in issues if _issue_scope(row) == "semantic"]
                other = [row for row in issues if _issue_scope(row) == "other"]
                control_outcomes.append(
                    {
                        "input_path": input_path,
                        "in_scope_alert": bool(scoped),
                        "in_scope_findings": [_finding_value(row) for row in scoped],
                        "semantic_alert": bool(semantic),
                        "semantic_findings": [_finding_value(row) for row in semantic],
                        "other_findings": [_finding_value(row) for row in other],
                    }
                )
                continue

            expected = frozenset(
                alias
                for label in _expected_labels(truth)
                for alias in normalize_finding(label)
            )
            expected_refs = _expected_refs(truth)
            category_rows = [
                row for row in issues if _issue_aliases(row).intersection(expected)
            ]
            anchor_rows = [
                row for row in issues if _row_refs(row).intersection(expected_refs)
            ]
            strict_rows = [
                row
                for row in category_rows
                if _row_refs(row).intersection(expected_refs)
            ]
            sources = sorted({_source(row) for row in category_rows})
            operator = _operator(truth) or "unknown"
            counts = operator_counts.setdefault(operator, Counter())
            counts["cases"] += 1
            counts["category_hits"] += bool(category_rows)
            counts["anchor_hits"] += bool(anchor_rows)
            counts["strict_hits"] += bool(strict_rows)
            positive_outcomes.append(
                {
                    "input_path": input_path,
                    "operator": operator,
                    "expected": sorted(expected),
                    "expected_refs": sorted(expected_refs),
                    "category_hit": bool(category_rows),
                    "anchor_hit": bool(anchor_rows),
                    "category_and_anchor_hit": bool(strict_rows),
                    "category_hit_sources": sources,
                    "matched_findings": [_finding_value(row) for row in category_rows],
                }
            )

        positives = len(positive_outcomes)
        controls = len(control_outcomes)
        category_hits = sum(row["category_hit"] for row in positive_outcomes)
        anchor_hits = sum(row["anchor_hit"] for row in positive_outcomes)
        strict_hits = sum(row["category_and_anchor_hit"] for row in positive_outcomes)
        in_scope_alerts = sum(row["in_scope_alert"] for row in control_outcomes)
        semantic_alerts = sum(row["semantic_alert"] for row in control_outcomes)
        source_attribution = Counter(
            "both"
            if row["category_hit_sources"] == ["deterministic", "llm"]
            else row["category_hit_sources"][0]
            if row["category_hit_sources"]
            else "none"
            for row in positive_outcomes
        )
        output[pipeline] = {
            "records": len(rows),
            "positive_cases": positives,
            "control_cases": controls,
            "target_category": {
                "matched": category_hits,
                "expected": positives,
                "recall": _ratio(category_hits, positives),
            },
            "target_anchor": {
                "matched": anchor_hits,
                "expected": positives,
                "recall": _ratio(anchor_hits, positives),
            },
            "target_category_and_anchor": {
                "matched": strict_hits,
                "expected": positives,
                "recall": _ratio(strict_hits, positives),
            },
            "target_category_source_attribution": {
                key: source_attribution.get(key, 0)
                for key in ("deterministic", "llm", "both", "none")
            },
            "operators": {
                operator: {
                    "cases": counts["cases"],
                    "category_hits": counts["category_hits"],
                    "category_recall": _ratio(counts["category_hits"], counts["cases"]),
                    "anchor_hits": counts["anchor_hits"],
                    "anchor_recall": _ratio(counts["anchor_hits"], counts["cases"]),
                }
                for operator, counts in sorted(operator_counts.items())
            },
            "control_alerts": {
                "policy": "only_structural_formal_alerts_are_in_scope_false_positives",
                "in_scope_alerted_cases": in_scope_alerts,
                "in_scope_false_positive_rate": _ratio(in_scope_alerts, controls),
                "semantic_alerted_cases": semantic_alerts,
                "semantic_alert_rate_descriptive": _ratio(semantic_alerts, controls),
            },
            "usage": {
                **dict(tokens),
                "calls_missing_usage": calls_missing_usage,
                "complete": calls_missing_usage == 0,
            },
            "latency_ms": {
                "mean_per_record": sum(durations) / len(durations)
                if durations
                else None,
                "total": sum(durations) if durations else None,
            },
            "error_count": errors,
            "positive_outcomes": positive_outcomes,
            "control_outcomes": control_outcomes,
        }
        outcome_maps[pipeline] = {row["input_path"]: row for row in positive_outcomes}

    comparisons: dict[str, Any] = {}
    for left, right in (
        ("full", "llm_only"),
        ("full", "deterministic"),
        ("llm_only", "deterministic"),
    ):
        common = sorted(set(outcome_maps[left]).intersection(outcome_maps[right]))
        differences = [
            float(outcome_maps[left][case]["category_hit"])
            - float(outcome_maps[right][case]["category_hit"])
            for case in common
        ]
        comparisons[f"{left}_minus_{right}"] = {
            "paired_cases": len(common),
            "left_only_hits": sum(value > 0 for value in differences),
            "right_only_hits": sum(value < 0 for value in differences),
            "equal_cases": sum(value == 0 for value in differences),
            "mean_target_category_difference": _ratio(
                sum(differences), len(differences)
            ),
        }

    deterministic_hits = {
        path
        for path, row in outcome_maps["deterministic"].items()
        if row["category_hit"]
    }
    llm_hits = {
        path for path, row in outcome_maps["llm_only"].items() if row["category_hit"]
    }
    output["comparisons"] = comparisons
    output["replacement_retention"] = {
        "deterministic_targets_detected": len(deterministic_hits),
        "retained_by_llm_only": len(deterministic_hits.intersection(llm_hits)),
        "lost_by_llm_only": len(deterministic_hits - llm_hits),
        "gained_by_llm_only": len(llm_hits - deterministic_hits),
    }
    return output


def analyze_e5(panel_paths: dict[str, Path], dataset_root: Path) -> dict[str, Any]:
    """Return panel-specific and pooled E5 metrics."""

    records_by_panel = {name: _read_records(path) for name, path in panel_paths.items()}
    panels = {
        name: _summarize(records, dataset_root)
        for name, records in sorted(records_by_panel.items())
    }
    all_records = [
        record for records in records_by_panel.values() for record in records
    ]
    return {
        "schema_version": "e5-analysis-v1",
        "policy": {
            "target_truth": "one_injected_structural_or_formal_defect_per_positive_case",
            "extras": "unadjudicated_and_not_scored_as_false_positives",
            "control_false_positives": "structural_formal_alerts_only",
            "semantic_control_alerts": "descriptive_only",
            "pooling": "panels_reported_separately_and_combined_preserves_case_pairing",
        },
        "panel_paths": {name: str(path) for name, path in sorted(panel_paths.items())},
        "panels": panels,
        "combined": _summarize(all_records, dataset_root),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument(
        "--panel",
        action="append",
        required=True,
        metavar="NAME=RESULTS_JSONL",
        help="named panel; repeat for screening and holdout",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    panel_paths: dict[str, Path] = {}
    for value in args.panel:
        if "=" not in value:
            parser.error("--panel must be NAME=RESULTS_JSONL")
        name, raw_path = value.split("=", 1)
        if not name or name in panel_paths:
            parser.error(f"invalid or duplicate panel name: {name!r}")
        panel_paths[name] = Path(raw_path)
    try:
        result = analyze_e5(panel_paths, args.dataset_root)
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
