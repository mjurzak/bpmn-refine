"""Repair proposal endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.experiments import CONVERTER_VERSION, ExperimentConfig, RunBlock, build_run_block
from app.llm.router import TaskType, resolve_model
from app.repair.ops import EditOp
from app.services.diagrams import export_bpmn_xml, parse_bpmn_bytes
from app.services.repair import dispatch_repair, repair_diagram, repair_prompt_path
from app.validation.rules import RULES_VERSION, ValidationIssue

router = APIRouter(prefix="/repair", tags=["repair"])


class RepairRequest(BaseModel):
    xml: str
    issues: list[ValidationIssue] = Field(default_factory=list)
    config: ExperimentConfig = Field(default_factory=ExperimentConfig)


class RepairResponse(BaseModel):
    updated_xml: str
    applied_ops: list[EditOp] = Field(default_factory=list)
    remaining_issues: list[ValidationIssue] = Field(default_factory=list)
    iterations: int
    converged: bool
    run: RunBlock


@router.post("", response_model=RepairResponse)
async def repair(req: RepairRequest) -> RepairResponse | JSONResponse:
    iterations = 0
    converged = False
    run = _build_repair_run(req.config, iterations=iterations, converged=converged)

    if not req.issues:
        return JSONResponse(
            status_code=400,
            content={
                "detail": "Repair requires at least one validation issue.",
                "run": run.model_dump(mode="json"),
            },
        )

    try:
        diagram = parse_bpmn_bytes(req.xml.encode("utf-8"))
        repair_result = await dispatch_repair(
            diagram,
            issues=req.issues,
            config=req.config,
            repair_fn=repair_diagram,
        )
        updated_xml = export_bpmn_xml(repair_result.repaired_diagram)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "run": run.model_dump(mode="json")},
        )

    iterations = repair_result.iterations
    remaining_issues = repair_result.remaining_issues
    converged = repair_result.converged
    run = _build_repair_run(req.config, iterations=iterations, converged=converged)

    return RepairResponse(
        updated_xml=updated_xml,
        applied_ops=repair_result.applied_ops,
        remaining_issues=remaining_issues,
        iterations=iterations,
        converged=converged,
        run=run,
    )


def _build_repair_run(
    config: ExperimentConfig,
    iterations: int,
    converged: bool,
) -> RunBlock:
    return build_run_block(
        config=config,
        model_used=resolve_model(TaskType.REPAIR, config=config),
        converter=CONVERTER_VERSION,
        rules_version=RULES_VERSION,
        prompt_files={"repair": repair_prompt_path()},
        iterations=iterations,
        converged=converged,
    )
