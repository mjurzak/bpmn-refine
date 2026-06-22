"""Reusable validation orchestration for API routes and CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from app.experiments import ExperimentConfig
from app.llm import client as llm_client
from app.llm.prompt_context import render_prompt_template
from app.llm.router import TaskType, resolve_model, resolve_provider
from app.model.schema import BpmnDiagram
from app.services.ir_payload import diagram_payload
from app.validation.checkers import run_tier2_checkers
from app.validation.rules import ValidationIssue, ValidationReport, issue_to_dict, validate

_PROMPT_DIR = Path(__file__).parent.parent / "llm" / "prompts"
_VALIDATE_PROMPT = _PROMPT_DIR / "validate.txt"


class ValidationResult(BaseModel):
    is_valid: bool
    issues: list[ValidationIssue]
    semantic_issues: list[ValidationIssue] = []


async def validate_diagram(
    diagram: BpmnDiagram,
    include_semantic: bool = False,
    include_t2: bool | None = None,
    config: ExperimentConfig | None = None,
) -> ValidationResult:
    """run deterministic validation and optionally an LLM semantic pass"""
    active_config = config or ExperimentConfig()
    report: ValidationReport = validate(diagram)
    checker_issues: list[ValidationIssue] = []
    semantic_issues: list[ValidationIssue] = []
    should_run_t2 = include_t2 if include_t2 is not None else active_config.tiers_enabled.t2

    if should_run_t2:
        checker_issues = await run_tier2_checkers(diagram, active_config)

    if include_semantic:
        semantic_issues = await _semantic_validate(diagram, report, config=active_config)

    issues = report.issues + checker_issues
    all_issues = issues + semantic_issues
    is_valid = not any(issue.severity == "error" for issue in all_issues)
    return ValidationResult(
        is_valid=is_valid,
        issues=issues,
        semantic_issues=semantic_issues,
    )


async def _semantic_validate(
    diagram: BpmnDiagram,
    report: ValidationReport,
    config: ExperimentConfig | None = None,
) -> list[ValidationIssue]:
    system_prompt = render_prompt_template(_VALIDATE_PROMPT, config=config)
    payload = {
        "ir_format": str((config or ExperimentConfig()).ir_format),
        "diagram": diagram_payload(diagram, config),
        "existing_issues": [issue_to_dict(issue) for issue in report.issues],
    }
    raw = await llm_client.complete(
        prompt=json.dumps(payload),
        system=system_prompt,
        model=resolve_model(TaskType.SEMANTIC_VALIDATION, config=config),
        provider=resolve_provider(TaskType.SEMANTIC_VALIDATION, config=config),
        reasoning_effort=str(config.reasoning_effort) if config and config.reasoning_effort else None,
    )
    try:
        issues_data = json.loads(raw)
        return [ValidationIssue(**issue) for issue in issues_data]
    except Exception:
        # surface malformed LLM output as a warning instead of crashing the command
        return [
            ValidationIssue(
                rule_id="LLM_PARSE_ERROR",
                severity="warning",
                message="LLM semantic validation returned an unparseable response.",
            )
        ]


def validate_prompt_name() -> str:
    return _VALIDATE_PROMPT.name


def validate_prompt_path() -> Path:
    return _VALIDATE_PROMPT
