"""Analyze an experiment ``results.jsonl`` against the evaluation truth.

This is intentionally a small, dependency-free post-processing tool.  It does
not import the application or contact a model provider.  The input path is
mirrored from ``<dataset>/variants/.../*.bpmn`` to
``<dataset>/ground_truth/.../*.json``.  Files below ``<dataset>/seeds`` are
controls; they contribute to clean-control metrics only when a verified
``ground_truth_overlay.json`` marks their baseline as clean.

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
import random
import re
import sys
from collections import Counter
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


def _case_id(input_path: str | Path) -> str:
    """Return the stable overlay key for a result input path."""

    path = Path(input_path)
    positions = [index for index, part in enumerate(path.parts) if part == "variants"]
    if positions:
        relative = Path(*path.parts[positions[-1] + 1 :])
        return relative.with_suffix("").as_posix()
    positions = [index for index, part in enumerate(path.parts) if part == "seeds"]
    if positions:
        relative = Path(*path.parts[positions[-1] :])
        return relative.with_suffix("").as_posix()
    return path.with_suffix("").as_posix()


def _load_overlay(
    dataset_root: Path, overlay_path: str | Path | None
) -> tuple[Path | None, dict[str, Any], Any]:
    """Load an optional versioned case-adjudication overlay.

    The accepted JSON forms are either ``{"cases": {case_id: record}}`` or
    a direct ``{case_id: record}`` mapping.  A list of records with a
    ``case_id`` field is accepted as a convenience for hand-authored files.
    Invalid/missing overlays are diagnostics, not a reason to score controls.
    """

    path = Path(overlay_path) if overlay_path is not None else dataset_root / "ground_truth_overlay.json"
    if not path.is_file():
        return (path if overlay_path is not None else None), {}, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return path, {}, None
    if not isinstance(payload, dict):
        return path, {}, None
    source = payload.get("cases", payload.get("records", payload))
    records: dict[str, Any] = {}
    if isinstance(source, dict):
        for key, value in source.items():
            if isinstance(key, str) and isinstance(value, dict):
                records[_case_id(key)] = value
    elif isinstance(source, list):
        for value in source:
            if isinstance(value, dict) and isinstance(value.get("case_id"), str):
                records[_case_id(value["case_id"])] = value
    return path, records, payload.get("schema_version", payload.get("version"))


def _overlay_record(records: dict[str, Any], input_path: str | Path) -> dict[str, Any] | None:
    value = records.get(_case_id(input_path))
    return value if isinstance(value, dict) else None


def _overlay_baseline(record: dict[str, Any] | None) -> tuple[str | None, bool]:
    if not record:
        return None, False
    baseline = record.get("baseline", record)
    if not isinstance(baseline, dict):
        return None, False
    status = baseline.get("status")
    human_verified = baseline.get("human_verified")
    return status if isinstance(status, str) else None, human_verified is True


def _expected_labels(truth: dict[str, Any]) -> list[str]:
    values = truth.get("expected_findings")
    if values is None:
        values = truth.get("expected_finding")
    if values is None:
        values = [
            defect.get(
                "expected_findings",
                defect.get(
                    "expected_finding",
                    defect.get("category", defect.get("rule_id")),
                ),
            )
            for defect in truth.get("defects", [])
            if isinstance(defect, dict)
        ]
    return _split_findings(values)


def _defect_refs(defect: dict[str, Any]) -> set[str]:
    localization = defect.get("localization")
    if isinstance(localization, dict) and "allowed_variant_refs" in localization:
        values = localization.get("allowed_variant_refs")
    else:
        values = defect.get("expected_elements", [])
    if isinstance(values, str):
        values = [values]
    return {
        str(ref)
        for ref in values
        if isinstance(ref, str) and ref
    } if isinstance(values, (list, tuple, set)) else set()


def _overlay_allowed_refs(
    overlay_record: dict[str, Any] | None,
    defect_index: int,
    defect: dict[str, Any],
    labels: Sequence[str],
) -> tuple[bool, set[str]]:
    if not overlay_record:
        return False, set()
    entries = overlay_record.get("injected", [])
    if not isinstance(entries, list):
        return False, set()
    operator = defect.get("operator")
    candidates = [entry for entry in entries if isinstance(entry, dict)]

    def entry_matches(entry: dict[str, Any]) -> bool:
        if isinstance(operator, str) and entry.get("operator") not in {None, operator}:
            return False
        entry_categories = entry.get(
            "categories", entry.get("category", entry.get("expected_finding"))
        )
        if entry_categories is None:
            return True
        return any(
            normalize_finding(entry_category).intersection(normalize_finding(label))
            for entry_category in _split_findings(entry_categories)
            for label in labels
        )

    indexed = candidates[defect_index] if defect_index < len(candidates) else None
    if indexed is not None and entry_matches(indexed):
        localization = indexed.get("localization")
        if isinstance(localization, dict) and "allowed_variant_refs" in localization:
            values = localization.get("allowed_variant_refs")
            if isinstance(values, str):
                values = [values]
            return (
                True,
                {
                    str(ref)
                    for ref in values
                    if isinstance(ref, str) and ref
                } if isinstance(values, (list, tuple, set)) else set(),
            )
    matching: list[dict[str, Any]] = []
    for entry in candidates:
        entry_index = entry.get("defect_index", entry.get("index"))
        if isinstance(entry_index, int) and entry_index == defect_index:
            matching.append(entry)
            continue
        if entry_matches(entry):
            matching.append(entry)
    if not matching and defect_index < len(candidates):
        matching = [candidates[defect_index]]
    if not matching:
        return False, set()
    localization = matching[0].get("localization")
    if not isinstance(localization, dict) or "allowed_variant_refs" not in localization:
        return False, set()
    values = localization.get("allowed_variant_refs")
    if isinstance(values, str):
        values = [values]
    return (
        True,
        {
            str(ref)
            for ref in values
            if isinstance(ref, str) and ref
        } if isinstance(values, (list, tuple, set)) else set(),
    )


def _expected_localizations(
    truth: dict[str, Any], overlay_record: dict[str, Any] | None = None
) -> list[tuple[list[str], set[str]]]:
    """Return finding-specific element references when the truth provides them."""

    result: list[tuple[list[str], set[str]]] = []
    for defect_index, defect in enumerate(truth.get("defects", [])):
        if not isinstance(defect, dict):
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
        has_overlay_refs, overlay_refs = _overlay_allowed_refs(
            overlay_record, defect_index, defect, labels
        )
        refs = overlay_refs if has_overlay_refs else _defect_refs(defect)
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
    reference_evidence: tuple[str, ...] = ()
    # Keep the source category for diagnostics (not for matching).  Aliases
    # such as ``unreachable_region`` intentionally expand to more than one
    # benchmark label, so the alias set alone is not a useful display value.
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


def _finding_value(row: dict[str, Any]) -> Any:
    """Return a row's finding identifier across result-schema generations."""

    for key in ("rule_id", "category", "finding_id", "id"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _display_category(value: Any) -> str | None:
    """Normalize a source category for human-readable count breakdowns."""

    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    if not lowered:
        return None
    if ":" in lowered:
        prefix, remainder = lowered.split(":", 1)
        if prefix in {"llm", "semantic", "rules", "deterministic"}:
            lowered = remainder.strip()
    return lowered or None


def _row_refs(row: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for key in ("element_refs", "affected_elements", "refs"):
        value = row.get(key, [])
        values = value if isinstance(value, (list, tuple, set)) else [value]
        refs.update(str(ref) for ref in values if isinstance(ref, str) and ref)
    if isinstance(row.get("element_id"), str) and row["element_id"]:
        refs.add(row["element_id"])
    return refs


def _row_reference_evidence(row: dict[str, Any]) -> tuple[str, ...]:
    raw = row.get("raw")
    values = raw.get("reference_evidence") if isinstance(raw, dict) else None
    if values is None:
        values = row.get("reference_evidence")
    if not isinstance(values, list):
        return ()
    return tuple(
        value
        for value in values
        if isinstance(value, str) and re.fullmatch(r"L[1-9]\d*", value)
    )


def _predictions(
    record: dict[str, Any], keys: Sequence[str] = ("pre_validation", "validation")
) -> list[_Prediction]:
    validation = _validation_payload(record, keys)
    rows = _issue_rows(validation)
    raw_ids = validation.get("issue_ids") if validation else None
    ids = list(raw_ids) if isinstance(raw_ids, list) else []

    # ``issue_ids`` is a compact summary and is not guaranteed to have the
    # same order as ``issues`` (the runner sorts it, while provider rows retain
    # response order).  Rows are therefore the source of truth for detailed
    # findings.  Consume one matching summary ID per row and append only IDs
    # that have no detailed row.  In particular, do not use ``next(row ...)``:
    # repeated categories must retain each row's own element references.
    consumed_ids: set[int] = set()
    predictions: list[_Prediction] = []

    def consume_summary(value: Any, row_index: int) -> None:
        if not ids:
            return
        aliases = normalize_finding(value)
        if row_index < len(ids) and row_index not in consumed_ids:
            indexed_aliases = normalize_finding(ids[row_index])
            if aliases and indexed_aliases.intersection(aliases):
                consumed_ids.add(row_index)
                return
        for index, candidate in enumerate(ids):
            if index in consumed_ids:
                continue
            if aliases and normalize_finding(candidate).intersection(aliases):
                consumed_ids.add(index)
                return

    for row_index, row in enumerate(rows):
        value = _finding_value(row)
        # A few old fixtures omitted the row-level category.  In that case the
        # positional ID is the only safe association available.
        if value is None and row_index < len(ids) and row_index not in consumed_ids:
            value = ids[row_index]
        consume_summary(value, row_index)
        for label in _split_findings(value):
            aliases = normalize_finding(label)
            if aliases:
                predictions.append(
                    _Prediction(
                        aliases=aliases,
                        refs=_row_refs(row),
                        reference_evidence=_row_reference_evidence(row),
                        category=_display_category(label),
                    )
                )

    # Summary-only findings still count, but have no localization evidence.
    for index, value in enumerate(ids):
        if index in consumed_ids:
            continue
        for label in _split_findings(value):
            aliases = normalize_finding(label)
            if aliases:
                predictions.append(
                    _Prediction(
                        aliases=aliases,
                        category=_display_category(label),
                    )
                )
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
        "include_semantic_projection": config.get("include_semantic_projection"),
        "repair_mode": config.get("repair_mode"),
        "max_repair_iters": config.get("max_repair_iters"),
    }


def _usage(
    record: dict[str, Any],
) -> tuple[int | None, int | None, int | None, int | None, int]:
    usage = record.get("usage")
    if not isinstance(usage, dict):
        return None, None, None, None, 1
    missing = usage.get("calls_missing_usage", 0)
    missing_count = int(missing) if isinstance(missing, (int, float)) and missing >= 0 else 1
    total = usage.get("total_tokens")
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    cached_input_tokens = usage.get("cached_input_tokens")
    return (
        int(total) if isinstance(total, (int, float)) else None,
        int(input_tokens) if isinstance(input_tokens, (int, float)) else None,
        int(output_tokens) if isinstance(output_tokens, (int, float)) else None,
        (
            int(cached_input_tokens)
            if isinstance(cached_input_tokens, (int, float))
            else None
        ),
        missing_count,
    )


def _comparable_total_tokens(
    provider: str | None, total_tokens: int, cached_input_tokens: int
) -> int:
    """Normalize the CLI conventions used for cross-provider comparison.

    Anthropic reports cache reads/writes outside ``input_tokens``. OpenAI/Codex
    reports cached input as a subset of input, so adding it there would count it
    twice.
    """

    return (
        total_tokens + cached_input_tokens
        if provider in {"anthropic", "claude_cli"}
        else total_tokens
    )


def _expected_refs(case: _Case, expected_index: int) -> set[str]:
    if expected_index >= len(case.expected_localizations):
        return set()
    return case.expected_localizations[expected_index][1]


def _exact_pairs(case: _Case) -> list[tuple[int, int]]:
    """Match a finding only when its category and refs match exactly.

    Category-only matching remains the primary benchmark score.  Exact
    equality is a diagnostic: a prediction with no refs, a partial set, or
    extra unrelated refs does not receive exact-localization credit.  Cases
    whose truth lacks refs remain visible in localization coverage diagnostics.
    """

    matches: dict[int, int] = {}

    def visit(pred_index: int, visited: set[int]) -> bool:
        for expected_index, aliases in enumerate(case.expected):
            if expected_index in visited:
                continue
            expected_refs = _expected_refs(case, expected_index)
            if not expected_refs:
                continue
            prediction = case.predicted[pred_index]
            if prediction.refs != expected_refs:
                continue
            if not prediction.aliases.intersection(aliases):
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

    for pred_index in range(len(case.predicted)):
        visit(pred_index, set())
    return sorted(matches.items())


def _strict_pairs(case: _Case) -> list[tuple[int, int]]:
    """Backward-compatible name for the exact-localization diagnostic."""

    return _exact_pairs(case)


def _anchor_pairs(case: _Case) -> list[tuple[int, int]]:
    """Match predicted and expected defects by overlapping element refs only."""

    matches: dict[int, int] = {}

    def visit(pred_index: int, visited: set[int]) -> bool:
        prediction = case.predicted[pred_index]
        for expected_index in range(len(case.expected)):
            if expected_index in visited:
                continue
            expected_refs = _expected_refs(case, expected_index)
            if not expected_refs or not prediction.refs.intersection(expected_refs):
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

    for pred_index in range(len(case.predicted)):
        visit(pred_index, set())
    return sorted(matches.items())


def _overlap_pairs(case: _Case) -> list[tuple[int, int]]:
    """Match category findings whose predicted refs overlap expected refs."""

    matches: dict[int, int] = {}

    def visit(pred_index: int, visited: set[int]) -> bool:
        prediction = case.predicted[pred_index]
        for expected_index, aliases in enumerate(case.expected):
            if expected_index in visited:
                continue
            expected_refs = _expected_refs(case, expected_index)
            if not expected_refs or not prediction.refs.intersection(expected_refs):
                continue
            if not prediction.aliases.intersection(aliases):
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

    for pred_index in range(len(case.predicted)):
        visit(pred_index, set())
    return sorted(matches.items())


def _case_pairs(case: _Case, mode: str) -> list[tuple[int, int]]:
    if mode == "exact":
        return _exact_pairs(case)
    if mode == "anchor":
        return _anchor_pairs(case)
    if mode == "overlap":
        return _overlap_pairs(case)
    return _matching_pairs(case.predicted, case.expected)


def _case_f1(
    case: _Case, *, strict: bool = False, mode: str | None = None
) -> float:
    if mode is None:
        mode = "exact" if strict else "category"
    pairs = _case_pairs(case, mode)
    precision = _metric(len(pairs), len(case.predicted))
    recall = _metric(len(pairs), len(case.expected))
    return _f1(precision, recall) or 0.0


def _finding_metrics(
    cases: Sequence[_Case], *, strict: bool = False, mode: str | None = None
) -> dict[str, Any]:
    if mode is None:
        mode = "exact" if strict else "category"
    injected = [case for case in cases if case.scored and case.expected]
    predicted_count = sum(len(case.predicted) for case in injected)
    expected_count = sum(len(case.expected) for case in injected)
    matched_count = 0
    per_case_f1: list[float] = []
    for case in injected:
        pairs = _case_pairs(case, mode)
        matched_count += len(pairs)
        per_case_f1.append(_case_f1(case, mode=mode))

    precision = _metric(matched_count, predicted_count)
    recall = _metric(matched_count, expected_count)
    return {
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
        "macro_f1": sum(per_case_f1) / len(per_case_f1) if per_case_f1 else None,
        "matched": matched_count,
        "predicted": predicted_count,
        "expected": expected_count,
    }


def _category_accuracy_given_anchor(cases: Sequence[_Case]) -> dict[str, Any]:
    injected = [case for case in cases if case.scored and case.expected]
    anchors = [
        (case, pred_index, expected_index)
        for case in injected
        for pred_index, expected_index in _anchor_pairs(case)
    ]
    correct = sum(
        bool(
            case.predicted[pred_index].aliases.intersection(
                case.expected[expected_index]
            )
        )
        for case, pred_index, expected_index in anchors
    )
    return {
        "accuracy": _metric(correct, len(anchors)),
        "correct": correct,
        "anchors": len(anchors),
        "expected": sum(len(case.expected) for case in injected),
        "predicted": sum(len(case.predicted) for case in injected),
    }


def _localization_metrics(cases: Sequence[_Case]) -> dict[str, Any]:
    injected = [case for case in cases if case.scored and case.expected]
    expected_count = sum(len(case.expected) for case in injected)
    expected_with_refs = sum(
        bool(_expected_refs(case, index))
        for case in injected
        for index in range(len(case.expected))
    )
    predicted_count = sum(len(case.predicted) for case in injected)
    predicted_with_refs = sum(bool(prediction.refs) for case in injected for prediction in case.predicted)
    category_pairs = [
        (case, pred_index, expected_index)
        for case in injected
        for pred_index, expected_index in _matching_pairs(case.predicted, case.expected)
    ]
    anchor_pairs = [
        (case, pred_index, expected_index)
        for case in injected
        for pred_index, expected_index in _anchor_pairs(case)
    ]
    matched_count = len(category_pairs)
    matched_with_refs = sum(
        bool(case.predicted[pred_index].refs and _expected_refs(case, expected_index))
        for case, pred_index, expected_index in category_pairs
    )
    overlap_count = sum(len(_overlap_pairs(case)) for case in injected)
    exact_count = sum(len(_exact_pairs(case)) for case in injected)
    expected_missing = expected_count - expected_with_refs
    predicted_missing = predicted_count - predicted_with_refs
    return {
        "coverage": _metric(predicted_with_refs, predicted_count),
        "missing": predicted_missing,
        "expected_coverage": _metric(expected_with_refs, expected_count),
        "expected_missing": expected_missing,
        "predicted_coverage": _metric(predicted_with_refs, predicted_count),
        "predicted_missing": predicted_missing,
        "matched_coverage": _metric(matched_with_refs, matched_count),
        "matched_missing": matched_count - matched_with_refs,
        "expected": expected_count,
        "expected_with_localization": expected_with_refs,
        "predicted": predicted_count,
        "predicted_with_localization": predicted_with_refs,
        "matched": matched_count,
        "matched_with_localization": matched_with_refs,
        "any_overlap_matched": len(anchor_pairs),
        "category_overlap_matched": overlap_count,
        "anchor_category_correct": sum(
            bool(
                case.predicted[pred_index].aliases.intersection(
                    case.expected[expected_index]
                )
            )
            for case, pred_index, expected_index in anchor_pairs
        ),
        "exact_matched": exact_count,
    }


def _clean_metrics(clean: Sequence[_Case]) -> dict[str, Any]:
    predictions = [prediction for case in clean for prediction in case.predicted]
    categories = Counter(
        prediction.category or (sorted(prediction.aliases)[0] if prediction.aliases else "unknown")
        for prediction in predictions
    )
    with_refs = sum(bool(prediction.refs) for prediction in predictions)
    return {
        "cases": len(clean),
        "false_positive_cases": sum(bool(case.predicted) for case in clean),
        "false_positive_rate": _metric(
            sum(bool(case.predicted) for case in clean), len(clean)
        ),
        "predicted_findings": len(predictions),
        "predictions_with_localization": with_refs,
        "missing_localization": len(predictions) - with_refs,
        "localization_coverage": _metric(with_refs, len(predictions)),
        "categories": dict(sorted(categories.items())),
    }


def _reference_evidence_metrics(cases: Sequence[_Case]) -> dict[str, Any]:
    predictions = [prediction for case in cases for prediction in case.predicted]
    with_evidence = [
        prediction for prediction in predictions if prediction.reference_evidence
    ]
    return {
        "predicted_findings": len(predictions),
        "findings_with_evidence": len(with_evidence),
        "findings_missing_evidence": len(predictions) - len(with_evidence),
        "coverage": _metric(len(with_evidence), len(predictions)),
        "line_citations": sum(
            len(prediction.reference_evidence) for prediction in predictions
        ),
    }


def _case_metrics(cases: Sequence[_Case]) -> dict[str, Any]:
    scored = [case for case in cases if case.scored]
    injected = [case for case in scored if case.expected]
    clean = [case for case in scored if not case.expected]
    verified_clean_controls = [
        case for case in cases if case.clean_control and case.scored
    ]
    unadjudicated = [case for case in cases if case.unadjudicated and case.clean_control]

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

    category_only = _finding_metrics(cases)
    localization_detection = _finding_metrics(cases, mode="anchor")
    category_localization = _finding_metrics(cases, mode="overlap")
    exact_localization = _finding_metrics(cases, mode="exact")
    matched_count = category_only["matched"]
    expected_count = category_only["expected"]
    predicted_count = category_only["predicted"]
    localization: list[float] = []
    for case in injected:
        pairs = _anchor_pairs(case)

        expected_locs = case.expected_localizations
        for pred_index, expected_index in pairs:
            pred_refs = case.predicted[pred_index].refs
            if not pred_refs or expected_index >= len(expected_locs):
                continue
            _, expected_refs = expected_locs[expected_index]
            if not expected_refs:
                continue
            localization.append(len(pred_refs & expected_refs) / len(pred_refs | expected_refs))

    localization_metrics = _localization_metrics(cases)
    localization_metrics.update(
        {
            "anchor": localization_detection,
            "category_overlap": category_localization,
            "exact": exact_localization,
            "category_accuracy_given_anchor": _category_accuracy_given_anchor(cases),
        }
    )
    clean_details = _clean_metrics(clean)
    finding_output = dict(category_only)
    # Keep the historical scalar fields at ``finding`` while also making the
    # two scoring views discoverable from that object for tabular consumers.
    finding_output.update(
        {
            "category_only": category_only,
            "localization_detection": localization_detection,
            "category_localization": category_localization,
            "exact_localization": exact_localization,
            # Historical aliases.  They intentionally point to the exact
            # diagnostic; new consumers should use ``localization_detection``
            # for the primary any-overlap score.
            "strict_localization": exact_localization,
            "strict": exact_localization,
        }
    )
    return {
        "injected_case_detection": {
            "precision": detection_precision,
            "recall": detection_recall,
            "f1": _f1(detection_precision, detection_recall),
            "true_positive": case_tp,
            "false_positive": case_fp,
            "false_negative": case_fn,
        },
        # ``finding`` is the historical name and remains category-only.
        "finding": finding_output,
        "category_only": category_only,
        "finding_category_only": category_only,
        "localization_detection": localization_detection,
        "localization_aware": localization_detection,
        "category_localization": category_localization,
        "exact_localization": exact_localization,
        "category_accuracy_given_anchor": _category_accuracy_given_anchor(cases),
        "finding_exact": exact_localization,
        "strict_localization": exact_localization,
        "finding_strict": exact_localization,
        "strict": exact_localization,
        "clean_false_positive_rate": _metric(case_fp, len(clean)),
        "localization_overlap": sum(localization) / len(localization) if localization else None,
        "localization": localization_metrics,
        # These scalar aliases make the most important coverage diagnostics
        # convenient for CSV/JSON consumers while the nested object retains
        # the complete injected-vs-predicted breakdown.
        "localization_coverage": localization_metrics["coverage"],
        "missing_localization": localization_metrics["missing"],
        "clean": clean_details,
        "clean_finding_count": clean_details["predicted_findings"],
        "clean_finding_categories": clean_details["categories"],
        "reference_evidence": _reference_evidence_metrics(cases),
        "injected_cases": len(injected),
        "clean_cases": len(clean),
        "verified_clean_controls": len(verified_clean_controls),
        "unadjudicated_controls": len(unadjudicated),
        "unadjudicated_cases": len(unadjudicated),
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


def _operator_labels(defect: dict[str, Any]) -> list[str]:
    return _split_findings(
        defect.get(
            "expected_findings",
            defect.get(
                "expected_finding",
                defect.get("category", defect.get("rule_id")),
            ),
        )
    )


def _operator_cases(cases: Sequence[_Case]) -> dict[str, list[_Case]]:
    """Project each injected case onto its construction operator(s)."""

    result: dict[str, list[_Case]] = {}
    for case in cases:
        if not case.scored or not case.expected or not case.truth:
            continue
        defects = [defect for defect in case.truth.get("defects", []) if isinstance(defect, dict)]
        grouped: dict[str, tuple[list[frozenset[str]], list[tuple[list[frozenset[str]], set[str]]]]] = {}
        if defects:
            for index, defect in enumerate(defects):
                operator = defect.get("operator")
                if not isinstance(operator, str) or not operator:
                    continue
                labels = _operator_labels(defect)
                if not labels and index < len(case.expected):
                    labels = [next(iter(case.expected[index]), "")]
                expected = [normalize_finding(label) for label in labels if normalize_finding(label)]
                refs = _expected_refs(case, index)
                localizations = [([normalize_finding(label)], refs) for label in labels]
                prior_expected, prior_localizations = grouped.get(operator, ([], []))
                grouped[operator] = (prior_expected + expected, prior_localizations + localizations)
        else:
            operators = [operator for operator in case.truth.get("operators", []) if isinstance(operator, str)]
            for index, operator in enumerate(operators):
                if index >= len(case.expected):
                    break
                expected = [case.expected[index]]
                localization = (
                    [case.expected_localizations[index]]
                    if index < len(case.expected_localizations)
                    else [([], set())]
                )
                prior_expected, prior_localizations = grouped.get(operator, ([], []))
                grouped[operator] = (prior_expected + expected, prior_localizations + localization)
        for operator, (expected, localizations) in grouped.items():
            result.setdefault(operator, []).append(
                _Case(
                    record=case.record,
                    truth=case.truth,
                    expected=expected,
                    expected_localizations=localizations,
                    predicted=case.predicted,
                    scored=True,
                    case_id=case.case_id,
                )
            )
    return result


def _operator_metrics(cases: Sequence[_Case]) -> dict[str, Any]:
    return {
        operator: _case_metrics(operator_cases)
        for operator, operator_cases in sorted(_operator_cases(cases).items())
    }


def _quantile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _bootstrap_difference(
    differences: Sequence[float], *, seed: int = 1729, replicates: int = 2000
) -> dict[str, Any] | None:
    """Deterministic paired bootstrap for a vector of per-case differences."""

    if not differences:
        return None
    observed = sum(differences) / len(differences)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(replicates):
        samples.append(
            sum(differences[rng.randrange(len(differences))] for _ in differences)
            / len(differences)
        )
    below_or_above_zero = sum(
        1 for sample in samples if (sample <= 0.0 if observed > 0.0 else sample >= 0.0)
    )
    p_value = min(1.0, 2.0 * below_or_above_zero / len(samples))
    lower = _quantile(samples, 0.025)
    upper = _quantile(samples, 0.975)
    return {
        "observed_difference": observed,
        "confidence_level": 0.95,
        "lower": lower,
        "upper": upper,
        "confidence_interval": [lower, upper],
        "ci95": [lower, upper],
        "p_value": p_value,
        "replicates": replicates,
        "seed": seed,
    }


def _paired_macro_f1_comparisons(
    grouped_cases: Sequence[tuple[dict[str, Any], Sequence[_Case]]]
) -> list[dict[str, Any]]:
    """Compare groups on common cases, retaining pairing through resampling."""

    comparisons: list[dict[str, Any]] = []
    for left_index, (left_config, left_cases) in enumerate(grouped_cases):
        left_by_id: dict[str, list[_Case]] = {}
        for case in left_cases:
            if case.scored and case.expected:
                left_by_id.setdefault(case.case_id, []).append(case)
        for right_config, right_cases in grouped_cases[left_index + 1 :]:
            right_by_id: dict[str, list[_Case]] = {}
            for case in right_cases:
                if case.scored and case.expected:
                    right_by_id.setdefault(case.case_id, []).append(case)
            differences: list[float] = []
            for case_id in sorted(set(left_by_id) & set(right_by_id)):
                # A duplicate input/repeat is unusual, but preserving result
                # order is deterministic and avoids silently dropping it.
                for left_case, right_case in zip(
                    left_by_id[case_id], right_by_id[case_id], strict=False
                ):
                    differences.append(_case_f1(left_case) - _case_f1(right_case))
            bootstrap = _bootstrap_difference(differences)
            comparisons.append(
                {
                    "left_config": left_config,
                    "right_config": right_config,
                    "cases": len(differences),
                    "difference_definition": "left_minus_right_per_case_macro_f1",
                    "bootstrap": bootstrap,
                }
            )
    return comparisons


def analyze_results(
    results_path: str | Path,
    dataset_root: str | Path,
    overlay_path: str | Path | None = None,
) -> dict[str, Any]:
    """Analyze valid JSONL records and return a deterministic JSON object."""

    results_path = Path(results_path)
    dataset_root = Path(dataset_root)
    overlay_file, overlay_records, overlay_version = _load_overlay(
        dataset_root, overlay_path
    )
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
            clean_control = _is_clean_seed(record["input_path"])
            overlay_record = _overlay_record(overlay_records, record["input_path"])
            baseline_status, baseline_verified = _overlay_baseline(overlay_record)
            verified_clean = (
                clean_control
                and baseline_status == "clean"
                and baseline_verified
            )
            unadjudicated = clean_control and not verified_clean
            scored = truth is not None or verified_clean
            expected_labels = _expected_labels(truth) if truth else []
            expected = [normalize_finding(value) for value in expected_labels]
            localization_rows = (
                _expected_localizations(truth, overlay_record) if truth else []
            )
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
                unadjudicated=unadjudicated,
                clean_control=clean_control,
                # Input path + repeat is stable across model/config groups;
                # trial_id is intentionally not used because it embeds the
                # experiment/config hash.
                case_id=json.dumps(
                    [record["input_path"], record.get("repeat", 0)],
                    separators=(",", ":"),
                ),
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
        operator_breakdown = _operator_metrics(cases)
        duration_values = [
            record.record.get("duration_ms")
            for record in cases
            if isinstance(record.record.get("duration_ms"), (int, float))
        ]
        token_values: list[int] = []
        input_values: list[int] = []
        output_values: list[int] = []
        cached_input_values: list[int] = []
        missing_usage = 0
        for case in cases:
            total, input_tokens, output_tokens, cached_input_tokens, missing = _usage(
                case.record
            )
            missing_usage += missing
            if total is not None:
                token_values.append(total)
            if input_tokens is not None:
                input_values.append(input_tokens)
            if output_tokens is not None:
                output_values.append(output_tokens)
            if cached_input_tokens is not None:
                cached_input_values.append(cached_input_tokens)
        errors = sum(bool(case.record.get("error")) for case in cases)
        complete_tokens = missing_usage == 0 and len(token_values) == len(cases)
        total_tokens = sum(token_values) if token_values else None
        cached_input_tokens = (
            sum(cached_input_values) if cached_input_values else 0
        )
        comparable_total_tokens = (
            _comparable_total_tokens(
                group["config"].get("provider"),
                total_tokens,
                cached_input_tokens,
            )
            if complete_tokens and total_tokens is not None
            else None
        )
        output_groups.append(
            {
                "config": group["config"],
                **metrics,
                "operator_breakdown": operator_breakdown,
                # Short alias for consumers that use the benchmark's
                # operator terminology directly.
                "operators": operator_breakdown,
                "paired_detection": _paired_metrics(cases),
                "repair": _repair_metrics(cases),
                "latency_ms": (
                    sum(duration_values) / len(duration_values) if duration_values else None
                ),
                "latency_ms_total": sum(duration_values) if duration_values else None,
                "tokens": total_tokens if complete_tokens else None,
                "comparable_total_tokens": comparable_total_tokens,
                "usage": {
                    "input_tokens": sum(input_values) if input_values else None,
                    "output_tokens": sum(output_values) if output_values else None,
                    "cached_input_tokens": (
                        cached_input_tokens if cached_input_values else None
                    ),
                    "total_tokens": total_tokens,
                    "comparable_total_tokens": comparable_total_tokens,
                    "calls_missing_usage": missing_usage,
                    "complete": complete_tokens,
                },
                "error_count": errors,
                "malformed_or_error_count": errors,
                "unscored_cases": sum(not case.scored for case in cases),
                "case_count": len(cases),
            }
        )

    grouped_cases = [
        (group["config"], group["cases"])
        for key in sorted(groups)
        for group in [groups[key]]
    ]
    paired_macro_f1 = _paired_macro_f1_comparisons(grouped_cases)

    uncertain_configs: set[str] = set()
    for comparison in paired_macro_f1:
        bootstrap = comparison.get("bootstrap")
        if not isinstance(bootstrap, dict):
            continue
        lower = bootstrap.get("lower")
        upper = bootstrap.get("upper")
        if not isinstance(lower, (int, float)) or not isinstance(upper, (int, float)):
            continue
        if lower <= 0.0 <= upper:
            for key in ("left_config", "right_config"):
                config = comparison.get(key)
                if isinstance(config, dict):
                    uncertain_configs.add(
                        json.dumps(config, sort_keys=True, separators=(",", ":"))
                    )

    for group in output_groups:
        config_key = json.dumps(
            group["config"], sort_keys=True, separators=(",", ":")
        )
        reasons: list[str] = []
        if group["verified_clean_controls"] == 0:
            reasons.append("no_verified_clean_controls")
        if group["error_count"] > 0:
            reasons.append("errors_present")
        if malformed_lines > 0:
            reasons.append("malformed_result_lines")
        if config_key in uncertain_configs:
            reasons.append("paired_macro_f1_ci_crosses_zero")
        group["ranking_eligible"] = not reasons
        group["ranking_ineligibility_reasons"] = reasons

    def ranking_key(group: dict[str, Any]) -> tuple[float, float, float, str]:
        macro_f1 = group["finding"]["macro_f1"]
        fpr = group["clean_false_positive_rate"]
        tokens = group["comparable_total_tokens"]
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
            "comparable_total_tokens": group["comparable_total_tokens"],
            "eligible": group["ranking_eligible"],
            "suppressed": not group["ranking_eligible"],
            "ineligibility_reasons": group["ranking_ineligibility_reasons"],
        }
        for group in sorted(output_groups, key=ranking_key)
    ]
    eligible_ranking = [item for item in ranking if item["eligible"]]
    winner = eligible_ranking[0]["config"] if eligible_ranking else None
    error_count = sum(group["error_count"] for group in output_groups)
    return {
        "results_path": str(results_path),
        "dataset_root": str(dataset_root),
        "ground_truth_overlay": {
            "path": str(overlay_file) if overlay_file else None,
            "version": overlay_version,
            "schema_version": overlay_version,
            "loaded": bool(overlay_records),
            "records": len(overlay_records),
        },
        "records_read": records_read,
        "malformed_lines": malformed_lines,
        "error_count": error_count,
        "malformed_or_error_count": malformed_lines + error_count,
        "groups": output_groups,
        "ranking": ranking,
        "winner": winner,
        "ranking_suppressed": not bool(eligible_ranking),
        "paired_macro_f1": paired_macro_f1,
        # Descriptive alias retained for callers that do not know the shorter
        # benchmark field name.
        "macro_f1_comparisons": paired_macro_f1,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="results.jsonl")
    parser.add_argument("dataset_root", type=Path, help="data/eval/v1.0")
    parser.add_argument(
        "--ground-truth-overlay",
        type=Path,
        help="optional adjudication overlay (defaults to dataset_root/ground_truth_overlay.json)",
    )
    parser.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    args = parser.parse_args(argv)
    try:
        result = analyze_results(
            args.results, args.dataset_root, overlay_path=args.ground_truth_overlay
        )
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
