"""Validation endpoints — deterministic rules first, then optional LLM pass."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.experiments import ExperimentConfig, RunBlock, build_run_block, converter_version
from app.llm.router import TaskType, resolve_model
from app.llm.tracing import LlmTrace, get_traces, reset_trace_context, start_trace_context
from app.model.schema import BpmnDiagram
from app.services.validation import (
    ValidationResult,
    validate_diagram as run_validation,
    validate_prompt_path,
)
from app.validation.checkers import checker_versions
from app.validation.rules import RULES_VERSION

router = APIRouter(prefix="/validate", tags=["validate"])


class ValidationRequest(BaseModel):
    diagram: BpmnDiagram
    include_semantic: bool = False
    config: ExperimentConfig = Field(default_factory=ExperimentConfig)


class ValidationResponse(ValidationResult):
    run: RunBlock
    llm_traces: list[LlmTrace] = Field(default_factory=list)


@router.post("", response_model=ValidationResponse)
async def validate_diagram(req: ValidationRequest) -> ValidationResponse | JSONResponse:
    """Run deterministic validation and optionally an LLM semantic pass."""
    include_semantic = req.include_semantic or req.config.tiers_enabled.t3
    include_t2 = req.config.tiers_enabled.t2
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
        checkers=checker_versions(req.config) if include_t2 else None,
    )
    trace_token = start_trace_context()
    try:
        result = await run_validation(
            req.diagram,
            include_semantic=include_semantic,
            include_t2=include_t2,
            config=req.config,
        )
    except Exception as exc:
        traces = get_traces()
        reset_trace_context(trace_token)
        return JSONResponse(
            status_code=500,
            content={
                "detail": str(exc),
                "run": run.model_dump(mode="json"),
                "llm_traces": [trace.model_dump(mode="json") for trace in traces],
            },
        )
    traces = get_traces()
    reset_trace_context(trace_token)
    return ValidationResponse(**result.model_dump(), run=run, llm_traces=traces)
