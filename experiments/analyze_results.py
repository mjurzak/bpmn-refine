"""Analyze an experiment ``results.jsonl`` against the evaluation truth.

This is intentionally a small, dependency-free post-processing tool.  It does
not import the application or contact a model provider.  The input path is
mirrored from ``<dataset>/variants/.../*.bpmn`` to
``<dataset>/ground_truth/.../*.json``.  Files below ``<dataset>/seeds`` are
clean controls and therefore have an empty expected-finding set.

Examples::

    python experiments/analyze_results.py \
        experiments/results/holistic/results.jsonl data/eval/v1.0
    python experiments/analyze_results.py results.jsonl data/eval/v1.0 \
        --output analysis.json

The JSON output is sorted and stable.  ``tokens`` is null when any counted
provider call omitted usage; known partial totals remain in ``usage`` for
diagnostics but are not used for ranking.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence


_RULE_ID = re.compile(r"^R\d{3}$")

# These aliases deliberately describe only the IDs in the benchmark contract.
# Unknown IDs are retained in normalized form instead of being guessed.
_SPECIAL_ALIASES: dict[str, frozenset[str]] = {
    "missing_start_event": frozenset({"R001"}),
    "missing_end_event": frozenset({"R002"}),
    "dangling_reference": frozenset({"R005", "R006"}),
    "deadlock": frozenset({"deadlock"}),
    "lack_of_synchronization": frozenset({"lack of synchronisation"}),
    "lack_of_synchronisation": frozenset({"lack of synchronisation"}),
    "improper_completion": frozenset({"improper completion"}),
    "improper_termination": frozenset({"improper completion"}),
    "unreachable_region": frozenset({"dead transition", "R007"}),
    "dead_transition": frozenset({"dead transition", "R007"}),
    "dead transition": frozenset({"dead transition", "R007"}),
}


def _split_findings(value: Any) -> list[str]:
    """Read one or many benchmark labels without inventing labels."""

    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(_split_findings(item))
        return values
    if not isinstance(value, str):
        return []
    return [part.strip() for part in value.split("+") if part.strip()]


def normalize_finding(value: Any) -> frozenset[str]:
    """Return the possible benchmark IDs represented by one finding ID.

    A set is used for the few intentionally equivalent IDs (for example an
    LLM ``unreachable_region`` can correspond to deterministic ``R007`` or
    the textual ``dead transition`` label).  Matching uses intersection and
    therefore never double-counts one prediction.
    """

    if not isinstance(value, str):
        return frozenset()
    raw = value.strip()
    if not raw:
        return frozenset()

    # Provider/source prefixes are part of the LLM-only experiment IDs.  The
    # rules prefix is accepted because some hand-authored fixtures retain it.
    lowered = raw.lower()
    if ":" in lowered:
        prefix, remainder = lowered.split(":", 1)
        if prefix in {"llm", "semantic", "rules", "deterministic"}:
            lowered = remainder.strip()

    if _RULE_ID.fullmatch(lowered.upper()):
        return frozenset({lowered.upper()})
    if lowered in _SPECIAL_ALIASES:
        return _SPECIAL_ALIASES[lowered]

    # Existing semantic categories are snake_case IDs.  Preserve that stable
    # vocabulary; only normalize harmless case and surrounding whitespace.
    return frozenset({lowered})


def ground_truth_path(input_path: str | Path, dataset_root: str | Path) -> Path | None:
    """Mirror a result input path to its ground-truth JSON path."""

    path = Path(input_path)
    root = Path(dataset_root)
    positions = [index for index, part in enumerate(path.parts) if part == "variants"]
    if not positions:
        return None
    relative = Path(*path.parts[positions[-1] + 1 :]).with_suffix(".json")
    return root / "ground_truth" / relative


def _is_clean_seed(input_path: str | Path) -> bool:
    return "seeds" in Path(input_path).parts


def _expected_labels(truth: dict[str, Any]) -> list[str]:
    values = truth.get("expected_findings")
    if values is None:
        values = truth.get("expected_finding")
    return _split_findings(values)


def _expected_localizations(truth: dict[str, Any]) -> list[tuple[list[str], set[str]]]:
    """Return finding-specific element references when the truth provides them."""

    result: list[tuple[list[str], set[str]]] = []
    for defect in truth.get("defects", []):
        if not isinstance(defect, dict):
            continue
        labels = _split_findings(
            defect.get("expected_findings", defect.get("expected_finding"))
        )
        refs = {
            str(ref)
            for ref in defect.get("expected_elements", [])
            if isinstance(ref, str) and ref
        }
        result.extend((labels, refs) for _ in labels)

    if result:
        return result
    labels = _expected_labels(truth)
    refs = {
        str(ref)
        for ref in truth.get("expected_elements", [])
        if isinstance(ref, str) and ref
    }
    return [(labels, refs) for _ in labels]


@dataclass
class _Prediction:
    aliases: frozenset[str]
    refs: set[str] = field(default_factory=set)


@dataclass
class _Case:
    record: dict[str, Any]
    truth: dict[str, Any] | None
    expected: list[frozenset[str]]
    expected_localizations: list[tuple[list[frozenset[str]], set[str]]]
    predicted: list[_Prediction]
    scored: bool


def _validation_payload(
    record: dict[str, Any], keys: Sequence[str] = ("pre_validation", "validation")
) -> dict[str, Any] | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, dict):
            return value
    return None


def _issue_rows(validation: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not validation:
        return []
    rows = validation.get("issues", [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _predictions(
    record: dict[str, Any], keys: Sequence[str] = ("pre_validation", "validation")
) -> list[_Prediction]:
    validation = _validation_payload(record, keys)
    rows = _issue_rows(validation)
    ids: list[Any] = []
    if validation and isinstance(validation.get("issue_ids"), list):
        ids.extend(validation["issue_ids"])
    ids.extend(row.get("rule_id", row.get("id")) for row in rows)

    predictions: list[_Prediction] = []
    seen: set[tuple[frozenset[str], tuple[str, ...]]] = set()
    for value in ids:
        aliases = normalize_finding(value)
        if not aliases:
            continue
        row = next(
            (candidate for candidate in rows if candidate.get("rule_id", candidate.get("id")) == value),
            None,
        )
        refs: set[str] = set()
        if row:
            for key in ("element_refs", "affected_elements", "refs"):
                candidate_refs = row.get(key, [])
                if isinstance(candidate_refs, list):
                    refs.update(str(ref) for ref in candidate_refs if isinstance(ref, str) and ref)
            if isinstance(row.get("element_id"), str) and row["element_id"]:
                refs.add(row["element_id"])
        signature = (aliases, tuple(sorted(refs)))
        if signature not in seen:
            seen.add(signature)
            predictions.append(_Prediction(aliases=aliases, refs=refs))
    return predictions


def _load_truth(input_path: str, dataset_root: Path) -> dict[str, Any] | None:
    path = ground_truth_path(input_path, dataset_root)
    if path is None or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _matching_pairs(
    predictions: Sequence[_Prediction], expected: Sequence[frozenset[str]]
) -> list[tuple[int, int]]:
    """Maximum one-to-one matching, deterministic under duplicate aliases."""

    matches: dict[int, int] = {}

    def visit(pred_index: int, visited: set[int]) -> bool:
        for expected_index, aliases in enumerate(expected):
            if expected_index in visited:
                continue
            if not predictions[pred_index].aliases.intersection(aliases):
                continue
            visited.add(expected_index)
            previous = next(
                (candidate for candidate, target in matches.items() if target == expected_index),
                None,
            )
            if previous is None or visit(previous, visited):
                matches[pred_index] = expected_index
                return True
        return False

    for pred_index in range(len(predictions)):
        visit(pred_index, set())
    return sorted(matches.items())


def _metric(value: int, denominator: int) -> float | None:
    return value / denominator if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _config_key(record: dict[str, Any]) -> dict[str, Any]:
    run = record.get("run") if isinstance(record.get("run"), dict) else {}
    config = run.get("config") if isinstance(run.get("config"), dict) else {}
    phases = record.get("phases") if isinstance(record.get("phases"), dict) else {}
    providers: list[str] = []
    for phase in phases.values():
        if not isinstance(phase, dict):
            continue
        for call in phase.get("calls", []):
            if isinstance(call, dict) and isinstance(call.get("provider"), str):
                providers.append(call["provider"])
    tiers = config.get("tiers_enabled")
    if not isinstance(tiers, dict):
        tiers = {"t1": None, "t2": None, "t3": None}
    return {
        "provider": config.get("provider_override") or (sorted(set(providers))[0] if providers else None),
        "model": (
            run.get("model_configured")
            or config.get("model_override")
            or run.get("model_used")
        ),
        "ir_format": config.get("ir_format"),
        "tiers_enabled": {
            "t1": tiers.get("t1"),
            "t2": tiers.get("t2"),
            "t3": tiers.get("t3"),
        },
        "scope": config.get("llm_validation_scope", "semantic"),
        "reasoning_effort": config.get("reasoning_effort"),
        "include_reference_description": config.get(
            "include_reference_description"
        ),
        "include_formal_evidence": config.get("include_formal_evidence"),
        "repair_mode": config.get("repair_mode"),
        "max_repair_iters": config.get("max_repair_iters"),
    }


def _usage(record: dict[str, Any]) -> tuple[int | None, int | None, int | None, int]:
    usage = record.get("usage")
    if not isinstance(usage, dict):
        return None, None, None, 1
    missing = usage.get("calls_missing_usage", 0)
    missing_count = int(missing) if isinstance(missing, (int, float)) and missing >= 0 else 1
    total = usage.get("total_tokens")
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    return (
        int(total) if isinstance(total, (int, float)) else None,
        int(input_tokens) if isinstance(input_tokens, (int, float)) else None,
        int(output_tokens) if isinstance(output_tokens, (int, float)) else None,
        missing_count,
    )


def _case_metrics(cases: Sequence[_Case]) -> dict[str, Any]:
    scored = [case for case in cases if case.scored]
    injected = [case for case in scored if case.expected]
    clean = [case for case in scored if not case.expected]

    # A noisy validator must not receive case-level credit merely for emitting
    # an unrelated finding on an injected diagram.  Detection requires at
    # least one benchmark-label match; clean controls still count any finding
    # as a false positive.
    case_tp = sum(
        bool(_matching_pairs(case.predicted, case.expected)) for case in injected
    )
    case_fp = sum(bool(case.predicted) for case in clean)
    case_fn = len(injected) - case_tp
    detection_precision = _metric(case_tp, case_tp + case_fp)
    detection_recall = _metric(case_tp, case_tp + case_fn)

    predicted_count = sum(len(case.predicted) for case in injected)
    expected_count = sum(len(case.expected) for case in injected)
    matched_count = 0
    per_case_f1: list[float] = []
    localization: list[float] = []
    for case in injected:
        pairs = _matching_pairs(case.predicted, case.expected)
        matched_count += len(pairs)
        precision = _metric(len(pairs), len(case.predicted))
        recall = _metric(len(pairs), len(case.expected))
        per_case_f1.append(_f1(precision, recall) or 0.0)

        expected_locs = case.expected_localizations
        for pred_index, expected_index in pairs:
            pred_refs = case.predicted[pred_index].refs
            if not pred_refs or expected_index >= len(expected_locs):
                continue
            possible_labels, expected_refs = expected_locs[expected_index]
            if not expected_refs:
                continue
            # Confirm the localization record corresponds to the matched label.
            if not any(
                case.predicted[pred_index].aliases.intersection(label)
                for label in possible_labels
            ):
                continue
            localization.append(len(pred_refs & expected_refs) / len(pred_refs | expected_refs))

    finding_precision = _metric(matched_count, predicted_count)
    finding_recall = _metric(matched_count, expected_count)
    return {
        "injected_case_detection": {
            "precision": detection_precision,
            "recall": detection_recall,
            "f1": _f1(detection_precision, detection_recall),
            "true_positive": case_tp,
            "false_positive": case_fp,
            "false_negative": case_fn,
        },
        "finding": {
            "precision": finding_precision,
            "recall": finding_recall,
            "f1": _f1(finding_precision, finding_recall),
            "macro_f1": sum(per_case_f1) / len(per_case_f1) if per_case_f1 else None,
            "matched": matched_count,
            "predicted": predicted_count,
            "expected": expected_count,
        },
        "clean_false_positive_rate": _metric(case_fp, len(clean)),
        "localization_overlap": sum(localization) / len(localization) if localization else None,
        "injected_cases": len(injected),
        "clean_cases": len(clean),
        "scored_cases": len(scored),
    }


def _paired_metrics(cases: Sequence[_Case]) -> dict[str, Any]:
    paired = [
        case
        for case in cases
        if case.scored
        and case.truth
        and case.truth.get("multiplicity") == 2
        and len(case.expected) == 2
    ]

    def summarize(selected: Sequence[_Case]) -> dict[str, Any]:
        any_count = 0
        all_count = 0
        exact_count = 0
        for case in selected:
            matched = len(_matching_pairs(case.predicted, case.expected))
            any_count += matched > 0
            all_count += matched == len(case.expected)
            exact_count += (
                matched == len(case.expected)
                and len(case.predicted) == len(case.expected)
            )
        count = len(selected)
        return {
            "cases": count,
            "any_detected": _metric(any_count, count),
            "all_detected": _metric(all_count, count),
            "exact_set": _metric(exact_count, count),
        }

    return {
        "overall": summarize(paired),
        "disjoint": summarize(
            [case for case in paired if case.truth.get("interaction") == "disjoint"]
        ),
        "interacting": summarize(
            [
                case
                for case in paired
                if case.truth.get("interaction") == "interacting"
            ]
        ),
    }


def _repair_metrics(cases: Sequence[_Case]) -> dict[str, Any]:
    attempted = [
        case
        for case in cases
        if case.scored and case.expected and isinstance(case.record.get("repair"), dict)
    ]
    applied = 0
    post_valid = 0
    complete = 0
    one_resolved = 0
    both_resolved = 0
    paired_count = 0
    for case in attempted:
        repair = case.record["repair"]
        applied += bool(repair.get("applied_ops")) and not repair.get("failed_ops")
        post = _validation_payload(case.record, ("post_validation",))
        post_valid += bool(post and post.get("is_valid"))
        remaining = len(
            _matching_pairs(
                _predictions(case.record, ("post_validation",)), case.expected
            )
        )
        resolved = len(case.expected) - remaining
        complete += resolved == len(case.expected)
        if len(case.expected) == 2:
            paired_count += 1
            one_resolved += resolved >= 1
            both_resolved += resolved == 2

    count = len(attempted)
    return {
        "attempted_cases": count,
        "application_success": _metric(applied, count),
        "post_validation_pass": _metric(post_valid, count),
        "complete_target_removal": _metric(complete, count),
        "paired_cases": paired_count,
        "one_resolved": _metric(one_resolved, paired_count),
        "both_resolved": _metric(both_resolved, paired_count),
    }


def analyze_results(results_path: str | Path, dataset_root: str | Path) -> dict[str, Any]:
    """Analyze valid JSONL records and return a deterministic JSON object."""

    results_path = Path(results_path)
    dataset_root = Path(dataset_root)
    groups: dict[str, dict[str, Any]] = {}
    malformed_lines = 0
    records_read = 0

    with results_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                malformed_lines += 1
                continue
            if not isinstance(record, dict) or not isinstance(record.get("input_path"), str):
                malformed_lines += 1
                continue
            records_read += 1
            truth = _load_truth(record["input_path"], dataset_root)
            scored = truth is not None or _is_clean_seed(record["input_path"])
            expected_labels = _expected_labels(truth) if truth else []
            expected = [normalize_finding(value) for value in expected_labels]
            localization_rows = _expected_localizations(truth) if truth else []
            expected_localizations = [
                ([normalize_finding(label) for label in labels], refs)
                for labels, refs in localization_rows
            ]
            case = _Case(
                record=record,
                truth=truth,
                expected=expected,
                expected_localizations=expected_localizations,
                predicted=_predictions(record),
                scored=scored,
            )
            key_config = _config_key(record)
            key = json.dumps(key_config, sort_keys=True, separators=(",", ":"))
            group = groups.setdefault(key, {"config": key_config, "cases": []})
            group["cases"].append(case)

    output_groups: list[dict[str, Any]] = []
    for key in sorted(groups):
        group = groups[key]
        cases: list[_Case] = group["cases"]
        metrics = _case_metrics(cases)
        duration_values = [
            record.record.get("duration_ms")
            for record in cases
            if isinstance(record.record.get("duration_ms"), (int, float))
        ]
        token_values: list[int] = []
        input_values: list[int] = []
        output_values: list[int] = []
        missing_usage = 0
        for case in cases:
            total, input_tokens, output_tokens, missing = _usage(case.record)
            missing_usage += missing
            if total is not None:
                token_values.append(total)
            if input_tokens is not None:
                input_values.append(input_tokens)
            if output_tokens is not None:
                output_values.append(output_tokens)
        errors = sum(bool(case.record.get("error")) for case in cases)
        complete_tokens = missing_usage == 0 and len(token_values) == len(cases)
        output_groups.append(
            {
                "config": group["config"],
                **metrics,
                "paired_detection": _paired_metrics(cases),
                "repair": _repair_metrics(cases),
                "latency_ms": (
                    sum(duration_values) / len(duration_values) if duration_values else None
                ),
                "latency_ms_total": sum(duration_values) if duration_values else None,
                "tokens": sum(token_values) if complete_tokens else None,
                "usage": {
                    "input_tokens": sum(input_values) if input_values else None,
                    "output_tokens": sum(output_values) if output_values else None,
                    "total_tokens": sum(token_values) if token_values else None,
                    "calls_missing_usage": missing_usage,
                    "complete": complete_tokens,
                },
                "error_count": errors,
                "malformed_or_error_count": errors,
                "unscored_cases": sum(not case.scored for case in cases),
                "case_count": len(cases),
            }
        )

    def ranking_key(group: dict[str, Any]) -> tuple[float, float, float, str]:
        macro_f1 = group["finding"]["macro_f1"]
        fpr = group["clean_false_positive_rate"]
        tokens = group["tokens"]
        config_json = json.dumps(group["config"], sort_keys=True, separators=(",", ":"))
        return (
            -(macro_f1 if macro_f1 is not None else -1.0),
            fpr if fpr is not None else 2.0,
            tokens if tokens is not None else float("inf"),
            config_json,
        )

    ranking = [
        {
            "config": group["config"],
            "macro_f1": group["finding"]["macro_f1"],
            "clean_false_positive_rate": group["clean_false_positive_rate"],
            "tokens": group["tokens"],
        }
        for group in sorted(output_groups, key=ranking_key)
    ]
    error_count = sum(group["error_count"] for group in output_groups)
    return {
        "results_path": str(results_path),
        "dataset_root": str(dataset_root),
        "records_read": records_read,
        "malformed_lines": malformed_lines,
        "error_count": error_count,
        "malformed_or_error_count": malformed_lines + error_count,
        "groups": output_groups,
        "ranking": ranking,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="results.jsonl")
    parser.add_argument("dataset_root", type=Path, help="data/eval/v1.0")
    parser.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    args = parser.parse_args(argv)
    try:
        result = analyze_results(args.results, args.dataset_root)
    except OSError as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
