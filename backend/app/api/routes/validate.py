"""Validation endpoints — deterministic rules first, then optional LLM pass."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.model.schema import BpmnDiagram
from app.services.validation import ValidationResult, validate_diagram as run_validation
from app.validation.rules import ValidationIssue

router = APIRouter(prefix="/validate", tags=["validate"])


class ValidationRequest(BaseModel):
    diagram: BpmnDiagram
    include_semantic: bool = False


class ValidationResponse(ValidationResult):
    pass


@router.post("", response_model=ValidationResponse)
async def validate_diagram(req: ValidationRequest) -> ValidationResponse:
    """Run deterministic validation and optionally an LLM semantic pass."""
    result = await run_validation(req.diagram, include_semantic=req.include_semantic)
    return ValidationResponse(**result.model_dump())
