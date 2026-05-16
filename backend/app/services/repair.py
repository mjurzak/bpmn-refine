"""Reusable repair orchestration for CLI and future API routes."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel

from app.experiments import ExperimentConfig
from app.history import service as hist
from app.llm import client as llm_client
from app.llm.router import TaskType, resolve_model, resolve_provider
from app.model.schema import BpmnDiagram
from app.repair.ops import EditOp, apply_edit_ops
from app.repair.quick_fixes import propose_quick_fix
from app.services.validation import validate_diagram
from app.validation.rules import ValidationIssue

_PROMPT_DIR = Path(__file__).parent.parent / "llm" / "prompts"
_REPAIR_PROMPT = _PROMPT_DIR / "repair.txt"


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


async def repair_diagram(
    diagram: BpmnDiagram,
    issues: list[ValidationIssue],
    session_id: str | None = None,
    config: ExperimentConfig | None = None,
    snapshot: bool = True,
) -> RepairResult:
    """repair a diagram using the existing repair prompt and issue list"""
    payload = {
        "diagram": diagram.model_dump(),
        "issues": [issue.__dict__ for issue in issues],
    }
    raw = await llm_client.complete(
        prompt=json.dumps(payload),
        system=_REPAIR_PROMPT.read_text(),
        model=resolve_model(TaskType.REPAIR, config=config),
        provider=resolve_provider(TaskType.REPAIR, config=config),
    )
    parsed = json.loads(raw)
    repaired_diagram = BpmnDiagram.model_validate(parsed["ir"])
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


async def dispatch_repair(
    diagram: BpmnDiagram,
    issues: list[ValidationIssue],
    config: ExperimentConfig | None = None,
    repair_fn: RepairFn | None = None,
) -> DispatcherRepairResult:
    """run the closed repair loop until convergence or iteration cap"""
    active_config = config or ExperimentConfig()
    active_repair_fn = repair_fn or repair_diagram
    current = diagram.model_copy(deep=True)
    remaining = list(issues)
    applied_ops: list[EditOp] = []
    iterations = 0

    while iterations < active_config.max_repair_iters:
        issue = _highest_priority_issue(remaining)
        if issue is None:
            break

        quick_fix_ops = propose_quick_fix(issue, current)
        if quick_fix_ops is not None:
            current, op_results = apply_edit_ops(quick_fix_ops, current)
            applied_ops.extend(result.op for result in op_results if result.applied)
        else:
            # until structured EditOp repair lands, fall back to the existing full-IR repair
            result = await active_repair_fn(
                current,
                issues=[issue],
                config=active_config,
                snapshot=False,
            )
            current = result.repaired_diagram

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


def repair_prompt_name() -> str:
    return _REPAIR_PROMPT.name


def repair_prompt_path() -> Path:
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
        return UnresolvedRepair(
            rule_id=item.get("rule_id") or item.get("id"),
            reason=item.get("reason") or item.get("message") or json.dumps(item),
        )
    return UnresolvedRepair(reason=str(item))
