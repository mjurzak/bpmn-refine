"""Compute the detection metrics used by the experiment reports."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence


_RULE_ID = re.compile(r"^R\d{3}$")
_ALIASES: dict[str, frozenset[str]] = {
    "missing_start_event": frozenset({"R001"}),
    "missing_end_event": frozenset({"R002"}),
    "dangling_reference": frozenset({"R005", "R006"}),
    "deadlock": frozenset({"deadlock"}),
    "lack_of_synchronization": frozenset({"lack of synchronisation"}),
    "lack_of_synchronisation": frozenset({"lack of synchronisation"}),
    "improper_completion": frozenset({"improper completion"}),
    "unreachable_region": frozenset({"dead transition", "R007"}),
    "dead_transition": frozenset({"dead transition", "R007"}),
    "dead transition": frozenset({"dead transition", "R007"}),
}


def _split_findings(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [label for item in value for label in _split_findings(item)]
    if not isinstance(value, str):
        return []
    return [part.strip() for part in value.split("+") if part.strip()]


def normalize_finding(value: Any) -> frozenset[str]:
    if not isinstance(value, str) or not value.strip():
        return frozenset()
    label = value.strip().lower()
    if ":" in label:
        prefix, remainder = label.split(":", 1)
        if prefix in {"llm", "semantic", "rules", "deterministic"}:
            label = remainder.strip()
    if _RULE_ID.fullmatch(label.upper()):
        return frozenset({label.upper()})
    return _ALIASES.get(label, frozenset({label}))


def ground_truth_path(input_path: str | Path, dataset_root: str | Path) -> Path | None:
    path = Path(input_path)
    try:
        index = len(path.parts) - 1 - path.parts[::-1].index("variants")
    except ValueError:
        return None
    return (
        Path(dataset_root)
        / "ground_truth"
        / Path(*path.parts[index + 1 :]).with_suffix(".json")
    )


def _case_id(input_path: str | Path) -> str:
    path = Path(input_path)
    for marker in ("variants", "seeds"):
        if marker in path.parts:
            index = len(path.parts) - 1 - path.parts[::-1].index(marker)
            start = index + 1 if marker == "variants" else index
            return Path(*path.parts[start:]).with_suffix("").as_posix()
    return path.with_suffix("").as_posix()


def _load_overlay(
    dataset_root: Path, overlay_path: str | Path | None
) -> tuple[Path | None, dict[str, Any], Any]:
    path = (
        Path(overlay_path)
        if overlay_path
        else dataset_root / "ground_truth_overlay.json"
    )
    if not path.is_file():
        return (path if overlay_path else None), {}, None
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records", {}) if isinstance(payload, dict) else {}
    return (
        path,
        records if isinstance(records, dict) else {},
        payload.get("schema_version"),
    )


def _overlay_record(
    records: dict[str, Any], input_path: str | Path
) -> dict[str, Any] | None:
    value = records.get(_case_id(input_path))
    return value if isinstance(value, dict) else None


def _expected_labels(truth: dict[str, Any]) -> list[str]:
    values = truth.get("expected_findings", truth.get("expected_finding"))
    if values is not None:
        return _split_findings(values)
    return [
        label
        for defect in truth.get("defects", [])
        if isinstance(defect, dict)
        for label in _split_findings(
            defect.get(
                "expected_findings",
                defect.get(
                    "expected_finding", defect.get("category", defect.get("rule_id"))
                ),
            )
        )
    ]


def _as_refs(value: Any) -> set[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return {item for item in values if isinstance(item, str) and item}


def _expected_localizations(
    truth: dict[str, Any], overlay: dict[str, Any] | None = None
) -> list[tuple[list[str], set[str]]]:
    injected = overlay.get("injected", []) if overlay else []
    rows: list[tuple[list[str], set[str]]] = []
    for index, defect in enumerate(truth.get("defects", [])):
        if not isinstance(defect, dict):
            continue
        labels = _split_findings(
            defect.get(
                "expected_findings",
                defect.get(
                    "expected_finding", defect.get("category", defect.get("rule_id"))
                ),
            )
        )
        localization = defect.get("localization", {})
        refs = _as_refs(
            localization.get(
                "allowed_variant_refs", defect.get("expected_elements", [])
            )
            if isinstance(localization, dict)
            else defect.get("expected_elements", [])
        )
        if index < len(injected) and isinstance(injected[index], dict):
            overlay_localization = injected[index].get("localization", {})
            if (
                isinstance(overlay_localization, dict)
                and "allowed_variant_refs" in overlay_localization
            ):
                refs = _as_refs(overlay_localization["allowed_variant_refs"])
        rows.extend((labels, refs) for _ in labels)
    if rows:
        return rows
    labels = _expected_labels(truth)
    refs = _as_refs(truth.get("expected_elements", []))
    return [(labels, refs) for _ in labels]


@dataclass
class _Prediction:
    aliases: frozenset[str]
    refs: set[str] = field(default_factory=set)
    reference_evidence: tuple[str, ...] = ()
    classification_basis: str | None = None
    category: str | None = None


@dataclass
class _Case:
    record: dict[str, Any]
    truth: dict[str, Any] | None
    expected: list[frozenset[str]]
    expected_localizations: list[tuple[list[frozenset[str]], set[str]]]
    predicted: list[_Prediction]
    scored: bool
    case_id: str = ""
    unadjudicated: bool = False
    clean_control: bool = False


def _validation_payload(
    record: dict[str, Any], keys: Sequence[str] = ("pre_validation", "validation")
) -> dict[str, Any] | None:
    return next(
        (record[key] for key in keys if isinstance(record.get(key), dict)), None
    )


def _issue_rows(validation: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = validation.get("issues", []) if validation else []
    return (
        [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    )


def _finding_value(row: dict[str, Any]) -> Any:
    return next(
        (
            row[key]
            for key in ("rule_id", "category", "finding_id", "id")
            if isinstance(row.get(key), str) and row[key].strip()
        ),
        None,
    )


def _display_category(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    label = value.strip().lower()
    if ":" in label and label.split(":", 1)[0] in {
        "llm",
        "semantic",
        "rules",
        "deterministic",
    }:
        label = label.split(":", 1)[1].strip()
    return label


def _row_refs(row: dict[str, Any]) -> set[str]:
    refs = set().union(
        *(
            _as_refs(row.get(key, []))
            for key in ("element_refs", "affected_elements", "refs")
        )
    )
    return refs | _as_refs(row.get("element_id"))


def _row_detail(row: dict[str, Any], key: str) -> Any:
    raw = row.get("raw")
    return raw.get(key) if isinstance(raw, dict) and key in raw else row.get(key)


def _predictions(
    record: dict[str, Any], keys: Sequence[str] = ("pre_validation", "validation")
) -> list[_Prediction]:
    validation = _validation_payload(record, keys)
    rows = _issue_rows(validation)
    ids = validation.get("issue_ids", []) if validation else []
    ids = ids if isinstance(ids, list) else []
    consumed: set[int] = set()
    predictions: list[_Prediction] = []

    for row_index, row in enumerate(rows):
        value = _finding_value(row)
        if value is None and row_index < len(ids):
            value = ids[row_index]
        aliases = normalize_finding(value)
        for index, candidate in enumerate(ids):
            if index not in consumed and aliases.intersection(
                normalize_finding(candidate)
            ):
                consumed.add(index)
                break
        evidence = _row_detail(row, "reference_evidence")
        evidence = (
            tuple(item for item in evidence if isinstance(item, str))
            if isinstance(evidence, list)
            else ()
        )
        basis = _row_detail(row, "classification_basis")
        for label in _split_findings(value):
            normalized = normalize_finding(label)
            if normalized:
                predictions.append(
                    _Prediction(
                        normalized,
                        _row_refs(row),
                        evidence,
                        basis if isinstance(basis, str) else None,
                        _display_category(label),
                    )
                )

    for index, value in enumerate(ids):
        if index not in consumed:
            predictions.extend(
                _Prediction(normalize_finding(label), category=_display_category(label))
                for label in _split_findings(value)
                if normalize_finding(label)
            )
    return predictions


def _load_truth(input_path: str, dataset_root: Path) -> dict[str, Any] | None:
    path = ground_truth_path(input_path, dataset_root)
    if path is None or not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _maximum_pairs(
    left_size: int, right_size: int, accepts: Callable[[int, int], bool]
) -> list[tuple[int, int]]:
    assigned: dict[int, int] = {}

    def visit(left: int, seen: set[int]) -> bool:
        for right in range(right_size):
            if right in seen or not accepts(left, right):
                continue
            seen.add(right)
            previous = next(
                (item for item, target in assigned.items() if target == right), None
            )
            if previous is None or visit(previous, seen):
                assigned[left] = right
                return True
        return False

    for left in range(left_size):
        visit(left, set())
    return sorted(assigned.items())


def _matching_pairs(
    predictions: Sequence[_Prediction], expected: Sequence[frozenset[str]]
) -> list[tuple[int, int]]:
    return _maximum_pairs(
        len(predictions),
        len(expected),
        lambda p, e: bool(predictions[p].aliases & expected[e]),
    )


def _expected_refs(case: _Case, index: int) -> set[str]:
    return (
        case.expected_localizations[index][1]
        if index < len(case.expected_localizations)
        else set()
    )


def _anchor_pairs(case: _Case) -> list[tuple[int, int]]:
    return _maximum_pairs(
        len(case.predicted),
        len(case.expected),
        lambda p, e: bool(_expected_refs(case, e) & case.predicted[p].refs),
    )


def _overlap_pairs(case: _Case) -> list[tuple[int, int]]:
    return _maximum_pairs(
        len(case.predicted),
        len(case.expected),
        lambda p, e: (
            bool(case.predicted[p].aliases & case.expected[e])
            and bool(_expected_refs(case, e) & case.predicted[p].refs)
        ),
    )


def _exact_pairs(case: _Case) -> list[tuple[int, int]]:
    return _maximum_pairs(
        len(case.predicted),
        len(case.expected),
        lambda p, e: (
            bool(case.predicted[p].aliases & case.expected[e])
            and bool(_expected_refs(case, e))
            and case.predicted[p].refs == _expected_refs(case, e)
        ),
    )


def _operator_cases(cases: Sequence[_Case]) -> dict[str, list[_Case]]:
    groups: dict[str, list[_Case]] = defaultdict(list)
    for case in cases:
        defects = [
            defect
            for defect in (case.truth or {}).get("defects", [])
            if isinstance(defect, dict)
        ]
        projected: dict[
            str,
            tuple[
                list[frozenset[str]],
                list[tuple[list[frozenset[str]], set[str]]],
            ],
        ] = {}
        for index, defect in enumerate(defects):
            operator = defect.get("operator")
            if not isinstance(operator, str):
                continue
            labels = _split_findings(
                defect.get(
                    "expected_findings",
                    defect.get(
                        "expected_finding",
                        defect.get("category", defect.get("rule_id")),
                    ),
                )
            )
            expected = [normalize_finding(label) for label in labels]
            refs = _expected_refs(case, index)
            prior_expected, prior_localizations = projected.get(operator, ([], []))
            projected[operator] = (
                prior_expected + expected,
                prior_localizations
                + [([normalize_finding(label)], refs) for label in labels],
            )
        for operator, (expected, localizations) in projected.items():
            groups[operator].append(
                _Case(
                    case.record,
                    case.truth,
                    expected,
                    localizations,
                    case.predicted,
                    True,
                    case.case_id,
                )
            )
    return dict(groups)


def _ratio(value: int | float, denominator: int | float) -> float | None:
    return value / denominator if denominator else None


def _metrics(cases: Sequence[_Case], mode: str) -> dict[str, Any]:
    selected = [case for case in cases if case.scored and case.expected]
    pairs = {
        "category": lambda case: _matching_pairs(case.predicted, case.expected),
        "anchor": _anchor_pairs,
        "overlap": _overlap_pairs,
        "exact": _exact_pairs,
    }
    matched = sum(len(pairs[mode](case)) for case in selected)
    predicted = sum(len(case.predicted) for case in selected)
    expected = sum(len(case.expected) for case in selected)
    precision, recall = _ratio(matched, predicted), _ratio(matched, expected)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else 0.0
    )
    return {
        "matched": matched,
        "predicted": predicted,
        "expected": expected,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _config(record: dict[str, Any]) -> dict[str, Any]:
    run = record.get("run", {})
    config = run.get("config", {}) if isinstance(run, dict) else {}
    return {
        "provider": config.get("provider_override"),
        "model": run.get("model_configured")
        or config.get("model_override")
        or run.get("model_used"),
        "ir_format": config.get("ir_format"),
        "tiers_enabled": config.get("tiers_enabled"),
        "scope": config.get("llm_validation_scope", "semantic"),
        "reasoning_effort": config.get("reasoning_effort"),
        "include_reference_description": config.get("include_reference_description"),
        "include_formal_evidence": config.get("include_formal_evidence"),
        "include_semantic_projection": config.get("include_semantic_projection"),
        "repair_mode": config.get("repair_mode"),
        "max_repair_iters": config.get("max_repair_iters"),
    }


def _case(record: dict[str, Any], dataset_root: Path, overlay: dict[str, Any]) -> _Case:
    input_path = record["input_path"]
    truth = _load_truth(input_path, dataset_root)
    labels = _expected_labels(truth) if truth else []
    localizations = (
        _expected_localizations(truth, _overlay_record(overlay, input_path))
        if truth
        else []
    )
    return _Case(
        record,
        truth,
        [normalize_finding(label) for label in labels],
        [
            ([normalize_finding(label) for label in row_labels], refs)
            for row_labels, refs in localizations
        ],
        _predictions(record),
        truth is not None,
        input_path,
        clean_control=truth is None,
    )


def _summarize(
    records: Sequence[dict[str, Any]], dataset_root: Path, overlay: dict[str, Any]
) -> dict[str, Any]:
    cases = [_case(record, dataset_root, overlay) for record in records]
    durations = [
        float(record["duration_ms"])
        for record in records
        if isinstance(record.get("duration_ms"), (int, float))
    ]
    token_values = [
        int(record["usage"]["total_tokens"])
        for record in records
        if isinstance(record.get("usage"), dict)
        and isinstance(record["usage"].get("total_tokens"), (int, float))
    ]
    controls = [case for case in cases if case.clean_control]
    return {
        "config": _config(records[0]),
        "cases": len(records),
        "category_only": _metrics(cases, "category"),
        "localization": {
            "anchor": _metrics(cases, "anchor"),
            "category_and_anchor": _metrics(cases, "overlap"),
            "exact": _metrics(cases, "exact"),
        },
        "controls": {
            "cases": len(controls),
            "alerted_cases": sum(bool(case.predicted) for case in controls),
            "alert_rate": _ratio(
                sum(bool(case.predicted) for case in controls), len(controls)
            ),
        },
        "latency_ms": {
            "mean": sum(durations) / len(durations) if durations else None,
            "total": sum(durations) if durations else None,
        },
        "usage": {
            "total_tokens": sum(token_values) if token_values else None,
            "complete": len(token_values) == len(records),
        },
        "errors": sum(bool(record.get("error")) for record in records),
    }


def analyze_results(
    results_path: Path, dataset_root: Path, overlay_path: Path | None = None
) -> dict[str, Any]:
    records = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if any(
        not isinstance(record, dict) or not isinstance(record.get("input_path"), str)
        for record in records
    ):
        raise ValueError("each result must be an object with input_path")
    overlay_file, overlay, overlay_version = _load_overlay(dataset_root, overlay_path)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[json.dumps(_config(record), sort_keys=True)].append(record)
    return {
        "schema_version": "analysis-v2",
        "results_path": str(results_path),
        "ground_truth_overlay": {
            "path": str(overlay_file) if overlay_file else None,
            "schema_version": overlay_version,
        },
        "groups": [
            _summarize(group, dataset_root, overlay) for group in grouped.values()
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument(
        "--ground-truth-overlay", "--overlay", dest="overlay", type=Path
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = analyze_results(args.results, args.dataset_root, args.overlay)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
