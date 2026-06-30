"""Reusable repair orchestration for CLI and future API routes."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.experiments import ExperimentConfig, RepairMode
from app.history import service as hist
from app.llm import client as llm_client
from app.llm.prompt_context import render_prompt_template
from app.llm.router import TaskType, resolve_model, resolve_provider
from app.llm.schema import strict_json_schema
from app.model.schema import BpmnDiagram
from app.repair.ops import (
    AtomicEditOpsResult,
    EditOp,
    ReplaceDiagramOp,
    apply_edit_ops,
    atomic_edit_op_list_adapter,
)
from app.repair.quick_fixes import propose_quick_fix
from app.services.ir_payload import diagram_payload, parse_diagram_payload
from app.services.validation import validate_diagram
from app.validation.rules import ValidationIssue, issue_to_dict

_PROMPT_DIR = Path(__file__).parent.parent / "llm" / "prompts"
_REPAIR_PROMPT = _PROMPT_DIR / "repair.txt"
_ATOMIC_REPAIR_PROMPT = _PROMPT_DIR / "repair_atomic.txt"

# computed once — the EditOp union has no open dicts, so it is fully strict-expressible
_ATOMIC_OPS_SCHEMA = strict_json_schema(AtomicEditOpsResult)


class UnresolvedRepair(BaseModel):
    rule_id: str | None = None
    reason: str


class RepairResult(BaseModel):
    repaired_diagram: BpmnDiagram
    unresolved: list[UnresolvedRepair] = []
    rev_id: str | None = None
    session_id: str | None = None


class DispatcherRepairResult(BaseModel):
    repaired_diagram: BpmnDiagram
    applied_ops: list[EditOp] = []
    remaining_issues: list[ValidationIssue] = []
    iterations: int = 0
    converged: bool = False


RepairFn = Callable[..., Awaitable[RepairResult]]
AtomicRepairFn = Callable[..., Awaitable[list[EditOp]]]
AtomicEditOpList = list[EditOp]


async def repair_diagram(
    diagram: BpmnDiagram,
    issues: list[ValidationIssue],
    session_id: str | None = None,
    config: ExperimentConfig | None = None,
    snapshot: bool = True,
) -> RepairResult:
    """repair a diagram using the existing repair prompt and issue list"""
    payload: dict[str, Any] = {
        "ir_format": str((config or ExperimentConfig()).ir_format),
        "diagram": diagram_payload(diagram, config),
        "issues": [issue_to_dict(issue) for issue in issues],
    }
    tier2_findings = _extract_tier2_findings(issues)
    if tier2_findings:
        payload["tier2_findings"] = tier2_findings
    raw = await llm_client.complete(
        prompt=json.dumps(payload),
        system=render_prompt_template(_REPAIR_PROMPT, config=config),
        model=resolve_model(TaskType.REPAIR, config=config),
        provider=resolve_provider(TaskType.REPAIR, config=config),
        reasoning_effort=str(config.reasoning_effort) if config and config.reasoning_effort else None,
    )
    parsed = json.loads(raw)
    repaired_diagram = parse_diagram_payload(parsed["ir"], config)
    unresolved = [_normalise_unresolved(item) for item in parsed.get("unresolved", [])]

    if not snapshot:
        return RepairResult(repaired_diagram=repaired_diagram, unresolved=unresolved)

    active_session_id = session_id
    new_session_id: str | None = None
    if active_session_id is None:
        active_session_id = hist.create_session()
        new_session_id = active_session_id

    revision = hist.snapshot(
        session_id=active_session_id,
        diagram=repaired_diagram,
        message="llm repair",
        author="llm",
    )

    return RepairResult(
        repaired_diagram=repaired_diagram,
        unresolved=unresolved,
        rev_id=revision.rev_id,
        session_id=new_session_id,
    )


async def repair_with_edit_ops(
    diagram: BpmnDiagram,
    issues: list[ValidationIssue],
    config: ExperimentConfig | None = None,
) -> AtomicEditOpList:
    """repair a diagram by asking the LLM for atomic EditOps"""
    payload: dict[str, Any] = {
        "ir_format": str((config or ExperimentConfig()).ir_format),
        "diagram": diagram_payload(diagram, config),
        "issues": [issue_to_dict(issue) for issue in issues],
        "repair_mode": RepairMode.ATOMIC,
    }
    tier2_findings = _extract_tier2_findings(issues)
    if tier2_findings:
        payload["tier2_findings"] = tier2_findings
    # structured outputs constrain the reply to the EditOp schema, so no fenced
    # scraping or best-effort JSON repair is needed
    parsed = await llm_client.complete_structured(
        prompt=json.dumps(payload),
        schema=_ATOMIC_OPS_SCHEMA,
        system=render_prompt_template(_ATOMIC_REPAIR_PROMPT, config=config),
        model=resolve_model(TaskType.REPAIR, config=config),
        provider=resolve_provider(TaskType.REPAIR, config=config),
        reasoning_effort=str(config.reasoning_effort) if config and config.reasoning_effort else None,
    )
    ops_data = parsed.get("ops", parsed) if isinstance(parsed, dict) else parsed
    return list(atomic_edit_op_list_adapter.validate_python(ops_data))


async def dispatch_repair(
    diagram: BpmnDiagram,
    issues: list[ValidationIssue],
    config: ExperimentConfig | None = None,
    repair_fn: RepairFn | None = None,
    atomic_repair_fn: AtomicRepairFn | None = None,
) -> DispatcherRepairResult:
    """run the closed repair loop until convergence or iteration cap"""
    active_config = config or ExperimentConfig()
    active_repair_fn = repair_fn or repair_diagram
    active_atomic_repair_fn = atomic_repair_fn or repair_with_edit_ops
    current = diagram.model_copy(deep=True)
    remaining = list(issues)
    applied_ops: list[EditOp] = []
    iterations = 0

    while iterations < active_config.max_repair_iters:
        issue = _highest_priority_issue(remaining)
        if issue is None:
            break

        if active_config.repair_mode == RepairMode.REGEN:
            result = await active_repair_fn(
                current,
                issues=[issue],
                config=active_config,
                snapshot=False,
            )
            current = result.repaired_diagram
            applied_ops.append(ReplaceDiagramOp(diagram=current))
        else:
            quick_fix_ops = propose_quick_fix(issue, current)
            ops = quick_fix_ops
            if ops is None:
                ops = await active_atomic_repair_fn(
                    current,
                    issues=[issue],
                    config=active_config,
                )
            current, op_results = apply_edit_ops(ops, current)
            applied_ops.extend(result.op for result in op_results if result.applied)

        validation = await validate_diagram(
            current,
            include_semantic=False,
            config=active_config,
        )
        remaining = validation.issues + validation.semantic_issues
        iterations += 1

    return DispatcherRepairResult(
        repaired_diagram=current,
        applied_ops=applied_ops,
        remaining_issues=remaining,
        iterations=iterations,
        converged=_has_no_errors(remaining),
    )


def repair_prompt_name(config: ExperimentConfig | None = None) -> str:
    return repair_prompt_path(config).name


def repair_prompt_path(config: ExperimentConfig | None = None) -> Path:
    if config is not None and config.repair_mode == RepairMode.ATOMIC:
        return _ATOMIC_REPAIR_PROMPT
    return _REPAIR_PROMPT


def _highest_priority_issue(issues: list[ValidationIssue]) -> ValidationIssue | None:
    actionable = [issue for issue in issues if issue.severity == "error"]
    if not actionable:
        return None
    return sorted(actionable, key=lambda issue: _severity_rank(issue), reverse=True)[0]


def _severity_rank(issue: ValidationIssue) -> int:
    ranks = {"error": 3, "warning": 2, "info": 1}
    return ranks.get(str(issue.severity), 0)


def _has_no_errors(issues: list[ValidationIssue]) -> bool:
    return not any(issue.severity == "error" for issue in issues)


def _normalise_unresolved(item: object) -> UnresolvedRepair:
    if isinstance(item, str):
        return UnresolvedRepair(reason=item)
    if isinstance(item, dict):
        rule_id = item.get("rule_id") or item.get("id")
        reason = item.get("reason") or item.get("message")
        return UnresolvedRepair(
            rule_id=str(rule_id) if rule_id is not None else None,
            reason=str(reason) if reason else json.dumps(item),
        )
    return UnresolvedRepair(reason=str(item))


def _extract_tier2_findings(issues: list[ValidationIssue]) -> list[dict[str, Any]]:
    """pull formal witnesses out of T2 issues for a dedicated payload section"""
    findings = []
    for issue in issues:
        if issue.formal_witness is None:
            continue
        element_refs = issue.element_refs or ([issue.element_id] if issue.element_id else [])
        findings.append(
            {
                "rule_id": issue.rule_id,
                "formal_witness": asdict(issue.formal_witness),
                "affected_elements": element_refs,
            }
        )
    return findings
