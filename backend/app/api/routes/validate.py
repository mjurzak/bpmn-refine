"""Validation endpoints — deterministic rules first, then optional LLM pass."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.experiments import ExperimentConfig, RunBlock, build_run_block, converter_version
from app.llm.router import TaskType, resolve_model
from app.model.schema import BpmnDiagram
from app.services.validation import (
    ValidationResult,
    validate_diagram as run_validation,
    validate_prompt_path,
)
from app.validation.rules import RULES_VERSION

router = APIRouter(prefix="/validate", tags=["validate"])


class ValidationRequest(BaseModel):
    diagram: BpmnDiagram
    include_semantic: bool = False
    config: ExperimentConfig = Field(default_factory=ExperimentConfig)


class ValidationResponse(ValidationResult):
    run: RunBlock


@router.post("", response_model=ValidationResponse)
async def validate_diagram(req: ValidationRequest) -> ValidationResponse | JSONResponse:
    """Run deterministic validation and optionally an LLM semantic pass."""
    include_semantic = req.include_semantic or req.config.tiers_enabled.t3
    run = build_run_block(
        config=req.config,
        model_used=(
            resolve_model(TaskType.SEMANTIC_VALIDATION, config=req.config)
            if include_semantic
            else "none"
        ),
        converter=converter_version(req.config),
        rules_version=RULES_VERSION,
        prompt_files={"validate": validate_prompt_path()} if include_semantic else None,
    )
    try:
        result = await run_validation(
            req.diagram,
            include_semantic=include_semantic,
            config=req.config,
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "run": run.model_dump(mode="json")},
        )
    return ValidationResponse(**result.model_dump(), run=run)
