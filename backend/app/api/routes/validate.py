"""Validation endpoints — deterministic rules first, then optional LLM pass."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app.model.schema import BpmnDiagram
from app.llm import client as llm_client
from app.llm.router import TaskType, resolve_model
from app.validation.rules import ValidationIssue, ValidationReport, validate

router = APIRouter(prefix="/validate", tags=["validate"])

_PROMPT_DIR = Path(__file__).parent.parent.parent / "llm" / "prompts"


class ValidationRequest(BaseModel):
    diagram: BpmnDiagram
    include_semantic: bool = False


class ValidationResponse(BaseModel):
    is_valid: bool
    issues: list[ValidationIssue]
    semantic_issues: list[ValidationIssue] = []


@router.post("", response_model=ValidationResponse)
async def validate_diagram(req: ValidationRequest) -> ValidationResponse:
    """Run deterministic validation and optionally an LLM semantic pass."""
    report: ValidationReport = validate(req.diagram)
    semantic_issues: list[ValidationIssue] = []

    if req.include_semantic:
        semantic_issues = await _semantic_validate(req.diagram, report)

    all_issues = report.issues + semantic_issues
    is_valid = not any(i.severity == "error" for i in all_issues)
    return ValidationResponse(is_valid=is_valid, issues=report.issues, semantic_issues=semantic_issues)


async def _semantic_validate(diagram: BpmnDiagram, report: ValidationReport) -> list[ValidationIssue]:
    system_prompt = (_PROMPT_DIR / "validate.txt").read_text()
    payload = {
        "diagram": diagram.model_dump(),
        "existing_issues": [i.__dict__ for i in report.issues],
    }
    raw = await llm_client.complete(
        prompt=json.dumps(payload),
        system=system_prompt,
        model=resolve_model(TaskType.SEMANTIC_VALIDATION),
    )
    try:
        issues_data = json.loads(raw)
        return [ValidationIssue(**d) for d in issues_data]
    except Exception:
        # if the LLM returns malformed JSON, surface a warning rather than crashing
        return [
            ValidationIssue(
                rule_id="LLM_PARSE_ERROR",
                severity="warning",
                message="LLM semantic validation returned an unparseable response.",
            )
        ]
