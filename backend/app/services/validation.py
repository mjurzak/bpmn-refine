"""Reusable validation orchestration for API routes and CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from app.experiments import ExperimentConfig
from app.llm import client as llm_client
from app.llm.router import TaskType, resolve_model, resolve_provider
from app.model.schema import BpmnDiagram
from app.validation.rules import ValidationIssue, ValidationReport, validate

_PROMPT_DIR = Path(__file__).parent.parent / "llm" / "prompts"
_VALIDATE_PROMPT = _PROMPT_DIR / "validate.txt"


class ValidationResult(BaseModel):
    is_valid: bool
    issues: list[ValidationIssue]
    semantic_issues: list[ValidationIssue] = []


async def validate_diagram(
    diagram: BpmnDiagram,
    include_semantic: bool = False,
    config: ExperimentConfig | None = None,
) -> ValidationResult:
    """run deterministic validation and optionally an LLM semantic pass"""
    report: ValidationReport = validate(diagram)
    semantic_issues: list[ValidationIssue] = []

    if include_semantic:
        semantic_issues = await _semantic_validate(diagram, report, config=config)

    all_issues = report.issues + semantic_issues
    is_valid = not any(issue.severity == "error" for issue in all_issues)
    return ValidationResult(
        is_valid=is_valid,
        issues=report.issues,
        semantic_issues=semantic_issues,
    )


async def _semantic_validate(
    diagram: BpmnDiagram,
    report: ValidationReport,
    config: ExperimentConfig | None = None,
) -> list[ValidationIssue]:
    system_prompt = _VALIDATE_PROMPT.read_text()
    payload = {
        "diagram": diagram.model_dump(),
        "existing_issues": [issue.__dict__ for issue in report.issues],
    }
    raw = await llm_client.complete(
        prompt=json.dumps(payload),
        system=system_prompt,
        model=resolve_model(TaskType.SEMANTIC_VALIDATION, config=config),
        provider=resolve_provider(TaskType.SEMANTIC_VALIDATION, config=config),
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
