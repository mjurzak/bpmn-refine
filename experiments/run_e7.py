"""Prepare, run, and summarize E7 from frozen E6 findings."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.dry_run import MockProvider
from app.experiment_runner import _call_record, _phase
from app.experiments import (
    ExperimentConfig,
    app_commit,
    config_hash,
    converter_version,
    hash_bytes,
    prompt_version,
)
from app.llm import client as llm_client
from app.llm.tracing import models_called, trace_usage
from app.model.schema import BpmnDiagram
from app.services.diagrams import parse_bpmn_bytes
from app.services.repair import (
    _matching_target_findings,
    dispatch_repair,
    repair_prompt_path,
)
from app.validation.rules import (
    FormalWitness,
    Severity,
    TraceStep,
    ValidationIssue,
    ValidationTier,
)
from experiments.analyze_e6 import _load_cases
from experiments.analyze_results import (
    _finding_value,
    _issue_rows,
    _overlap_pairs,
    _split_findings,
    _validation_payload,
)

SELECTION_SCHEMA = "e7-selection-v1"
RESULT_SCHEMA = "e7-result-v1"
SALT = "E7-v1"
GROUP_LIMIT = 2


def _atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _group(truth: dict[str, Any]) -> str | None:
    class_name = {"SEM": "semantic", "STRUCT": "structural", "SOUND": "formal"}.get(
        truth.get("class")
    )
    shape = truth.get("interaction")
    if class_name is None or shape not in {"single", "disjoint", "interacting"}:
        return None
    if shape == "interacting" and class_name == "semantic":
        return None
    return f"{shape}_{class_name}"


def _prediction_row_indices(record: dict[str, Any]) -> list[int | None]:
    validation = _validation_payload(record, ("pre_validation", "validation"))
    rows = _issue_rows(validation)
    indices: list[int | None] = []
    for row_index, row in enumerate(rows):
        labels = _split_findings(_finding_value(row))
        indices.extend([row_index] * len(labels))
    # E6 has detailed rows for every finding.  Keep defensive placeholders for
    # legacy summary-only IDs so a malformed source cannot silently select one.
    predicted_count = len(record.get("pre_validation", {}).get("issue_ids", []))
    if len(indices) < predicted_count:
        indices.extend([None] * (predicted_count - len(indices)))
    return indices


def prepare_selection(
    e6_results: Path, dataset_root: Path, output: Path
) -> dict[str, Any]:
    cases, _, _ = _load_cases(e6_results, dataset_root)
    eligible: dict[str, list[Any]] = defaultdict(list)
    eligibility_counts: dict[str, int] = defaultdict(int)
    for case in cases:
        group = _group(case.truth or {})
        if group is None:
            continue
        pairs = _overlap_pairs(case)
        if len(pairs) != len(case.expected):
            continue
        row_indices = _prediction_row_indices(case.record)
        selected_rows = sorted(
            {
                row_indices[pred_index]
                for pred_index, _ in pairs
                if pred_index < len(row_indices) and row_indices[pred_index] is not None
            }
        )
        if not selected_rows:
            continue
        case._e7_issue_indices = selected_rows  # type: ignore[attr-defined]
        eligible[group].append(case)
        eligibility_counts[group] += 1

    selected: list[dict[str, Any]] = []
    for group, group_cases in sorted(eligible.items()):
        group_cases.sort(
            key=lambda case: hashlib.sha256(
                f"{SALT}|{group}|{case.record['input_path']}".encode()
            ).hexdigest()
        )
        for case in group_cases[:GROUP_LIMIT]:
            validation = _validation_payload(case.record, ("pre_validation",)) or {}
            rows = _issue_rows(validation)
            truth_path = _truth_path(case.record["input_path"], dataset_root)
            selected.append(
                {
                    "case_id": case.truth["variant_id"],
                    "group": group,
                    "input_path": case.record["input_path"],
                    "description_path": case.record.get("description_path"),
                    "truth_path": str(truth_path),
                    "e6_trial_id": case.record["trial_id"],
                    "e6_input_hash": case.record["input_hash"],
                    "e6_issue_indices": case._e7_issue_indices,  # type: ignore[attr-defined]
                    "eligible_issues": [
                        rows[index] for index in case._e7_issue_indices
                    ],  # type: ignore[attr-defined]
                    "e6_all_issues": rows,
                    "expected_findings": case.truth.get("expected_findings", []),
                    "expected_elements": case.truth.get("expected_elements", []),
                }
            )

    required = [
        "single_semantic",
        "single_structural",
        "single_formal",
        "disjoint_semantic",
        "disjoint_structural",
        "disjoint_formal",
        "interacting_structural",
        "interacting_formal",
    ]
    payload = {
        "schema_version": SELECTION_SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "source_results": str(e6_results),
        "source_results_hash": hash_bytes(e6_results.read_bytes()),
        "dataset_root": str(dataset_root),
        "selection_policy": {
            "eligibility": "all injected targets matched by category and overlapping anchor in E6",
            "sampling": f"up to {GROUP_LIMIT} per group by sha256({SALT}|group|input_path)",
            "finding_input": "only the E6 findings matched to injected targets",
        },
        "required_groups": required,
        "eligible_counts": {
            group: eligibility_counts.get(group, 0) for group in required
        },
        "selected_counts": {
            group: sum(case["group"] == group for case in selected)
            for group in required
        },
        "unavailable_groups": [
            group for group in required if not eligibility_counts.get(group)
        ],
        "cases": selected,
    }
    _atomic_write(output, payload)
    return payload


def _truth_path(input_path: str, dataset_root: Path) -> Path:
    parts = Path(input_path).parts
    marker = parts.index("variants")
    return (
        dataset_root / "ground_truth" / Path(*parts[marker + 1 :]).with_suffix(".json")
    )


def _issue(value: dict[str, Any]) -> ValidationIssue:
    witness_value = value.get("formal_witness")
    witness = None
    if isinstance(witness_value, dict):
        witness = FormalWitness(
            kind=str(witness_value.get("kind", "unknown")),
            description=str(witness_value.get("description", "")),
            trace=[TraceStep(**step) for step in witness_value.get("trace", [])],
            counterexample_traces=[
                [TraceStep(**step) for step in trace]
                for trace in witness_value.get("counterexample_traces", [])
            ],
            marking=dict(witness_value.get("marking", {})),
            dead_elements=list(witness_value.get("dead_elements", [])),
            uncovered_elements=list(witness_value.get("uncovered_elements", [])),
        )
    return ValidationIssue(
        rule_id=str(value["rule_id"]),
        severity=Severity(value["severity"]),
        message=str(value.get("message", "")),
        tier=ValidationTier(value["tier"]) if value.get("tier") else None,
        element_id=value.get("element_id"),
        suggestion=value.get("suggestion"),
        element_refs=list(value.get("element_refs", [])),
        source=value.get("source"),
        formal_witness=witness,
        raw=value.get("raw"),
    )


def _issues_json(issues: list[ValidationIssue]) -> list[dict[str, Any]]:
    return [json.loads(json.dumps(asdict(issue), default=str)) for issue in issues]


def _repair_json(result: Any) -> dict[str, Any]:
    return {
        "iterations": result.iterations,
        "converged": result.converged,
        "errors_resolved": result.errors_resolved,
        "stop_reason": str(result.stop_reason),
        "applied_ops": [op.model_dump(mode="json") for op in result.applied_ops],
        "applied_op_origins": [str(value) for value in result.applied_op_origins],
        "failed_ops": [
            {"op": row.op.model_dump(mode="json"), "error": row.error}
            for row in result.failed_ops
        ],
        "rejected_ops": [op.model_dump(mode="json") for op in result.rejected_ops],
        "regression_issues": _issues_json(result.regression_issues),
        "rolled_back": result.rolled_back,
        "remaining_issues": _issues_json(result.remaining_issues),
        "diagram": result.repaired_diagram.model_dump(mode="json"),
    }


async def _run_case(
    case: dict[str, Any], config: ExperimentConfig, mock: bool
) -> dict[str, Any]:
    started = time.perf_counter()
    input_path = Path(case["input_path"])
    description = (
        Path(case["description_path"]).read_text(encoding="utf-8")
        if case.get("description_path")
        else None
    )
    diagram = parse_bpmn_bytes(input_path.read_bytes())
    issues = [_issue(row) for row in case["eligible_issues"]]
    all_issues = [_issue(row) for row in case["e6_all_issues"]]
    target_scoped = str(config.repair_loop_policy) == "target_scoped_safe"
    traces = []
    first: Any = None
    final: Any = None
    error = None
    original_provider = llm_client.get_provider
    if mock:
        provider = MockProvider()
        llm_client.get_provider = lambda name=None: provider
    try:
        async with _phase(traces):
            first = await dispatch_repair(
                diagram,
                all_issues if target_scoped else issues,
                config=config,
                single_plan=True,
                reference_description=description,
                assigned_issues=issues if target_scoped else None,
            )
        final = first
        remaining_budget = max(0, config.max_repair_iters - first.iterations)
        remaining_targets = (
            _matching_target_findings(issues, first.remaining_issues)
            if target_scoped
            else first.remaining_issues
        )
        if remaining_targets and remaining_budget and not first.rolled_back:
            continuation_config = config.model_copy(
                update={"max_repair_iters": remaining_budget}
            )
            async with _phase(traces):
                continuation = await dispatch_repair(
                    first.repaired_diagram,
                    first.remaining_issues,
                    config=continuation_config,
                    reference_description=description,
                    assigned_issues=remaining_targets if target_scoped else None,
                )
            # Combine bookkeeping while keeping the actual final state/results.
            continuation.iterations += first.iterations
            continuation.applied_ops = first.applied_ops + continuation.applied_ops
            continuation.applied_op_origins = (
                first.applied_op_origins + continuation.applied_op_origins
            )
            continuation.failed_ops = first.failed_ops + continuation.failed_ops
            continuation.failed_op_origins = (
                first.failed_op_origins + continuation.failed_op_origins
            )
            continuation.rejected_ops = first.rejected_ops + continuation.rejected_ops
            continuation.regression_issues = (
                first.regression_issues + continuation.regression_issues
            )
            continuation.rolled_back = first.rolled_back or continuation.rolled_back
            final = continuation
    except Exception as exc:  # the checkpoint is the experiment output
        error = f"{type(exc).__name__}: {exc}"
    finally:
        llm_client.get_provider = original_provider

    return {
        "schema_version": RESULT_SCHEMA,
        "case_id": case["case_id"],
        "group": case["group"],
        "input_path": case["input_path"],
        "input_hash": hash_bytes(input_path.read_bytes()),
        "description_path": case.get("description_path"),
        "description_hash": hash_bytes((description or "").encode()),
        "truth_path": case["truth_path"],
        "e6_trial_id": case["e6_trial_id"],
        "e6_input_hash": case["e6_input_hash"],
        "eligible_issues": case["eligible_issues"],
        "e6_all_issues": case["e6_all_issues"],
        "config": config.model_dump(mode="json"),
        "config_hash": config_hash(config),
        "converter": converter_version(config),
        "app_commit": app_commit(),
        "prompt_version": prompt_version(repair_prompt_path(config)).model_dump(
            mode="json"
        ),
        "first_plan": _repair_json(first) if first is not None else None,
        "bounded_result": _repair_json(final) if final is not None else None,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "model_used": models_called(traces),
        "calls": [
            _call_record(trace, False).model_dump(mode="json") for trace in traces
        ],
        "usage": trace_usage(traces).model_dump(mode="json"),
        "error": error,
    }


async def run_selection(
    selection_path: Path,
    out_dir: Path,
    config: ExperimentConfig,
    *,
    concurrency: int = 2,
    mock: bool = False,
    resume: bool = True,
) -> dict[str, Any]:
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    cases = selection["cases"]
    trials_dir = out_dir / "trials"
    trials_dir.mkdir(parents=True, exist_ok=True)
    existing = {path.stem for path in trials_dir.glob("*.json")} if resume else set()
    pending = [
        case for case in cases if case["case_id"].replace("/", "__") not in existing
    ]
    semaphore = asyncio.Semaphore(concurrency)

    async def execute(case: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            result = await _run_case(case, config, mock)
            _atomic_write(
                trials_dir / f"{case['case_id'].replace('/', '__')}.json", result
            )
            return result

    results = await asyncio.gather(*(execute(case) for case in pending))
    stored = {
        json.loads(path.read_text(encoding="utf-8"))["case_id"]: json.loads(
            path.read_text(encoding="utf-8")
        )
        for path in trials_dir.glob("*.json")
    }
    ordered = [stored[case["case_id"]] for case in cases if case["case_id"] in stored]
    (out_dir / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ordered),
        encoding="utf-8",
    )
    manifest = {
        "experiment_id": "e7-repair",
        "selection_path": str(selection_path),
        "selection_hash": hash_bytes(selection_path.read_bytes()),
        "config": config.model_dump(mode="json"),
        "config_hash": config_hash(config),
        "app_commit": app_commit(),
        "planned": len(cases),
        "completed": len(ordered),
        "executed_this_run": len(results),
        "failed": sum(bool(row.get("error")) for row in ordered),
        "concurrency": concurrency,
        "mock": mock,
        "usage": {
            key: sum(int((row.get("usage") or {}).get(key, 0)) for row in ordered)
            for key in (
                "calls",
                "calls_missing_usage",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "cached_input_tokens",
                "reasoning_tokens",
            )
        },
    }
    _atomic_write(out_dir / "manifest.json", manifest)
    return manifest


def analyze(results_path: Path, selection_path: Path) -> dict[str, Any]:
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selected = {case["case_id"]: case for case in selection["cases"]}
    records = [
        json.loads(line) for line in results_path.read_text().splitlines() if line
    ]

    def element_map(diagram: BpmnDiagram) -> dict[str, dict[str, Any]]:
        values: dict[str, dict[str, Any]] = {}
        for process in diagram.processes:
            for node in process.flow_nodes:
                payload = node.model_dump(mode="json")
                # Incoming/outgoing arrays are derived adjacency. A necessary
                # repair flow changes its endpoint's array without changing the
                # endpoint itself, so preservation compares intrinsic node data.
                payload.pop("incoming", None)
                payload.pop("outgoing", None)
                values[str(node.id)] = payload
            for flow in process.sequence_flows:
                values[str(flow.id)] = flow.model_dump(mode="json")
        return values

    def issue_signature(issue: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
        refs = set(issue.get("element_refs") or [])
        if issue.get("element_id"):
            refs.add(issue["element_id"])
        return str(issue.get("rule_id")), tuple(sorted(str(ref) for ref in refs))

    def deterministic_error_signatures(
        issues: list[dict[str, Any]],
    ) -> set[tuple[str, tuple[str, ...]]]:
        return {
            issue_signature(issue)
            for issue in issues
            if issue.get("severity") == "error"
            and issue.get("source") in {"rules", "woflan"}
        }

    def deterministic_warning_signatures(
        issues: list[dict[str, Any]],
    ) -> set[tuple[str, tuple[str, ...]]]:
        return {
            issue_signature(issue)
            for issue in issues
            if issue.get("severity") == "warning"
            and issue.get("source") in {"rules", "woflan"}
        }

    def target_remaining(record: dict[str, Any], key: str) -> int:
        phase = record.get(key) or {}
        fake = {
            "pre_validation": {
                "issues": phase.get("remaining_issues", []),
                "issue_ids": [],
            }
        }
        case_source = selected[record["case_id"]]
        truth = json.loads(Path(case_source["truth_path"]).read_text(encoding="utf-8"))
        # Reuse E6's matching implementation by making a one-record temporary case.
        from experiments.analyze_results import (
            _Case,
            _expected_labels,
            _expected_localizations,
            _load_overlay,
            _overlay_record,
            _predictions,
            normalize_finding,
        )

        _, overlay, _ = _load_overlay(Path(selection["dataset_root"]), None)
        localizations = _expected_localizations(
            truth, _overlay_record(overlay, record["input_path"])
        )
        case = _Case(
            record=fake,
            truth=truth,
            expected=[normalize_finding(value) for value in _expected_labels(truth)],
            expected_localizations=[
                ([normalize_finding(value) for value in labels], refs)
                for labels, refs in localizations
            ],
            predicted=_predictions(fake),
            scored=True,
            case_id=record["case_id"],
        )
        return len(_overlap_pairs(case))

    outcomes = []
    for record in records:
        expected = len(selected[record["case_id"]]["expected_findings"])
        case_source = selected[record["case_id"]]
        truth = json.loads(Path(case_source["truth_path"]).read_text(encoding="utf-8"))
        first_remaining = (
            target_remaining(record, "first_plan")
            if not record.get("error")
            else expected
        )
        final_remaining = (
            target_remaining(record, "bounded_result")
            if not record.get("error")
            else expected
        )
        first_phase = record.get("first_plan") or {}
        bounded = record.get("bounded_result") or {}
        first_issues = first_phase.get("remaining_issues", [])
        final_issues = bounded.get("remaining_issues", [])
        initial = parse_bpmn_bytes(Path(record["input_path"]).read_bytes())
        first_diagram = (
            BpmnDiagram.model_validate(first_phase["diagram"])
            if isinstance(first_phase.get("diagram"), dict)
            else initial
        )
        final_diagram = (
            BpmnDiagram.model_validate(bounded["diagram"])
            if isinstance(bounded.get("diagram"), dict)
            else initial
        )
        initial_elements = element_map(initial)
        first_elements = element_map(first_diagram)
        final_elements = element_map(final_diagram)
        footprint = set(truth.get("injection_site", [])) | set(
            truth.get("expected_elements", [])
        )
        eligible_preservation = sorted(set(initial_elements) - footprint)
        unchanged = sum(
            final_elements.get(element_id) == initial_elements[element_id]
            for element_id in eligible_preservation
        )
        first_unchanged = sum(
            first_elements.get(element_id) == initial_elements[element_id]
            for element_id in eligible_preservation
        )
        label_stable = sum(
            final_elements.get(element_id, {}).get("name")
            == initial_elements[element_id].get("name")
            for element_id in eligible_preservation
            if "name" in initial_elements[element_id]
        )
        label_denominator = sum(
            "name" in initial_elements[element_id]
            for element_id in eligible_preservation
        )
        initial_deterministic = deterministic_error_signatures(
            case_source["e6_all_issues"]
        )
        first_deterministic = deterministic_error_signatures(first_issues)
        final_deterministic = deterministic_error_signatures(final_issues)
        new_first_deterministic = sorted(first_deterministic - initial_deterministic)
        new_deterministic = sorted(final_deterministic - initial_deterministic)
        initial_warnings = deterministic_warning_signatures(
            case_source["e6_all_issues"]
        )
        final_warnings = deterministic_warning_signatures(final_issues)
        new_warnings = sorted(final_warnings - initial_warnings)
        strict_preservation = unchanged == len(eligible_preservation)
        first_strict_preservation = first_unchanged == len(eligible_preservation)
        manual_review = (
            record["group"].endswith("_semantic") and not strict_preservation
        )
        outcomes.append(
            {
                "case_id": record["case_id"],
                "group": record["group"],
                "expected_targets": expected,
                "first_resolved": expected - first_remaining,
                "final_resolved": expected - final_remaining,
                "first_complete": first_remaining == 0,
                "first_errors_resolved": first_phase.get("errors_resolved"),
                "first_new_deterministic_errors": len(new_first_deterministic),
                "first_preservation_rate": (
                    first_unchanged / len(eligible_preservation)
                    if eligible_preservation
                    else None
                ),
                "first_strict_unrelated_preservation": first_strict_preservation,
                "final_complete": final_remaining == 0,
                "iterations": bounded.get("iterations"),
                "stop_reason": bounded.get("stop_reason"),
                "rolled_back": bounded.get("rolled_back", False),
                "rejected_ops": len(bounded.get("rejected_ops", [])),
                "regression_issues": len(bounded.get("regression_issues", [])),
                "applied_ops": len(bounded.get("applied_ops", [])),
                "converged": bounded.get("converged"),
                "errors_resolved": bounded.get("errors_resolved"),
                "remaining_errors": sum(
                    issue.get("severity") == "error" for issue in final_issues
                ),
                "remaining_warnings": sum(
                    issue.get("severity") == "warning" for issue in final_issues
                ),
                "new_deterministic_errors": len(new_deterministic),
                "new_deterministic_error_signatures": new_deterministic,
                "new_deterministic_warnings": len(new_warnings),
                "new_deterministic_warning_signatures": new_warnings,
                "preservation_eligible_elements": len(eligible_preservation),
                "preserved_unchanged_elements": unchanged,
                "preservation_rate": (
                    unchanged / len(eligible_preservation)
                    if eligible_preservation
                    else None
                ),
                "label_stability_rate": (
                    label_stable / label_denominator if label_denominator else None
                ),
                "strict_unrelated_preservation": strict_preservation,
                "manual_review": "required" if manual_review else "not_required",
                "first_safe_target_success": (
                    first_remaining == 0
                    and first_phase.get("errors_resolved") is True
                    and not new_first_deterministic
                ),
                "final_safe_target_success": (
                    final_remaining == 0
                    and bounded.get("errors_resolved") is True
                    and not new_deterministic
                ),
                "error": record.get("error"),
            }
        )

    def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        targets = sum(row["expected_targets"] for row in rows)
        return {
            "cases": len(rows),
            "targets": targets,
            "first_resolved": sum(row["first_resolved"] for row in rows),
            "first_target_removal_rate": (
                sum(row["first_resolved"] for row in rows) / targets
                if targets
                else None
            ),
            "first_complete_cases": sum(row["first_complete"] for row in rows),
            "first_safe_target_success_cases": sum(
                row["first_safe_target_success"] for row in rows
            ),
            "final_resolved": sum(row["final_resolved"] for row in rows),
            "final_target_removal_rate": (
                sum(row["final_resolved"] for row in rows) / targets
                if targets
                else None
            ),
            "final_complete_cases": sum(row["final_complete"] for row in rows),
            "final_safe_target_success_cases": sum(
                row["final_safe_target_success"] for row in rows
            ),
            "converged_cases": sum(row["converged"] is True for row in rows),
            "rolled_back_cases": sum(row["rolled_back"] is True for row in rows),
            "rejected_ops": sum(row["rejected_ops"] for row in rows),
            "regression_issues": sum(row["regression_issues"] for row in rows),
            "errors_resolved_cases": sum(
                row["errors_resolved"] is True for row in rows
            ),
            "new_deterministic_errors": sum(
                row["new_deterministic_errors"] for row in rows
            ),
            "new_deterministic_warnings": sum(
                row["new_deterministic_warnings"] for row in rows
            ),
            "strict_preservation_cases": sum(
                row["strict_unrelated_preservation"] for row in rows
            ),
            "manual_review_cases": sum(
                row["manual_review"] == "required" for row in rows
            ),
            "mean_preservation_rate": (
                sum(
                    row["preservation_rate"]
                    for row in rows
                    if row["preservation_rate"] is not None
                )
                / sum(row["preservation_rate"] is not None for row in rows)
                if any(row["preservation_rate"] is not None for row in rows)
                else None
            ),
            "mean_label_stability_rate": (
                sum(
                    row["label_stability_rate"]
                    for row in rows
                    if row["label_stability_rate"] is not None
                )
                / sum(row["label_stability_rate"] is not None for row in rows)
                if any(row["label_stability_rate"] is not None for row in rows)
                else None
            ),
            "errors": sum(bool(row["error"]) for row in rows),
        }

    groups = sorted({row["group"] for row in outcomes})
    result = {
        "schema_version": "e7-analysis-v1",
        "policy": {
            "target_resolution": "absence of a post-repair category+anchor match",
            "unmatched_findings": "unadjudicated, not automatic new errors",
            "preservation": "exact IR equality for pre-existing elements outside the injected footprint",
            "new_errors": "new tier1/woflan error signatures only; LLM-only extras remain unadjudicated",
        },
        "overall": summary(outcomes),
        "by_group": {
            group: summary([row for row in outcomes if row["group"] == group])
            for group in groups
        },
        "unavailable_groups": selection.get("unavailable_groups", []),
        "outcomes": outcomes,
    }
    _atomic_write(results_path.parent / "analysis.json", result)
    return result


def _config(repair_loop_policy: str | None = None) -> ExperimentConfig:
    return ExperimentConfig.model_validate(
        {
            "model_tier": "custom",
            "model_override": "gpt-5.6-terra",
            "provider_override": "codex_cli",
            "ir_format": "compact_json",
            "tiers_enabled": {"t1": True, "t2": True, "t3": True},
            "llm_validation_scope": "holistic",
            "include_reference_description": True,
            "include_semantic_projection": True,
            "include_formal_evidence": True,
            "repair_mode": "atomic",
            "repair_loop_policy": repair_loop_policy,
            "max_repair_iters": 2,
            "reasoning_effort": "low",
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("e6_results", type=Path)
    prepare.add_argument("dataset_root", type=Path)
    prepare.add_argument("--out", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("selection", type=Path)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--concurrency", type=int, default=2)
    run.add_argument("--mock", action="store_true")
    run.add_argument("--no-resume", action="store_true")
    run.add_argument(
        "--repair-loop-policy",
        choices=("legacy_all_findings", "target_scoped_safe"),
    )
    report = sub.add_parser("analyze")
    report.add_argument("results", type=Path)
    report.add_argument("selection", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_selection(args.e6_results, args.dataset_root, args.out)
    elif args.command == "run":
        result = asyncio.run(
            run_selection(
                args.selection,
                args.out,
                _config(args.repair_loop_policy),
                concurrency=args.concurrency,
                mock=args.mock,
                resume=not args.no_resume,
            )
        )
    else:
        result = analyze(args.results, args.selection)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
