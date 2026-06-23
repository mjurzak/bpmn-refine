"""Repair proposal endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.experiments import ExperimentConfig, RunBlock, build_run_block, converter_version
from app.llm.router import TaskType, resolve_model
from app.llm.tracing import LlmTrace, get_traces, reset_trace_context, start_trace_context
from app.model.schema import BpmnDiagram
from app.repair.ops import EditOp, EditOpResult, apply_edit_ops
from app.services.diagrams import export_bpmn_xml, parse_bpmn_bytes
from app.services.repair import dispatch_repair, repair_diagram, repair_prompt_path
from app.validation.rules import RULES_VERSION, ValidationIssue

router = APIRouter(prefix="/repair", tags=["repair"])


class RepairRequest(BaseModel):
    xml: str
    issues: list[ValidationIssue] = Field(default_factory=list)
    config: ExperimentConfig = Field(default_factory=ExperimentConfig)


class RepairResponse(BaseModel):
    input_diagram: BpmnDiagram
    updated_xml: str
    updated_diagram: BpmnDiagram
    applied_ops: list[EditOp] = Field(default_factory=list)
    remaining_issues: list[ValidationIssue] = Field(default_factory=list)
    iterations: int
    converged: bool
    run: RunBlock
    llm_traces: list[LlmTrace] = Field(default_factory=list)


class ApplyEditOpsRequest(BaseModel):
    diagram: BpmnDiagram
    ops: list[EditOp] = Field(default_factory=list)


class ApplyEditOpsResponse(BaseModel):
    updated_diagram: BpmnDiagram
    updated_xml: str
    op_results: list[EditOpResult] = Field(default_factory=list)


@router.post("", response_model=RepairResponse)
async def repair(req: RepairRequest) -> RepairResponse | JSONResponse:
    iterations = 0
    converged = False
    run = _build_repair_run(req.config, iterations=iterations, converged=converged)

    trace_token = start_trace_context()
    if not req.issues:
        reset_trace_context(trace_token)
        return JSONResponse(
            status_code=400,
            content={
                "detail": "Repair requires at least one validation issue.",
                "run": run.model_dump(mode="json"),
                "llm_traces": [],
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

    iterations = repair_result.iterations
    remaining_issues = repair_result.remaining_issues
    converged = repair_result.converged
    run = _build_repair_run(req.config, iterations=iterations, converged=converged)
    traces = get_traces()
    reset_trace_context(trace_token)

    return RepairResponse(
        input_diagram=diagram,
        updated_xml=updated_xml,
        updated_diagram=repair_result.repaired_diagram,
        applied_ops=repair_result.applied_ops,
        remaining_issues=remaining_issues,
        iterations=iterations,
        converged=converged,
        run=run,
        llm_traces=traces,
    )


@router.post("/apply", response_model=ApplyEditOpsResponse)
async def apply_selected_edit_ops(req: ApplyEditOpsRequest) -> ApplyEditOpsResponse:
    updated_diagram, op_results = apply_edit_ops(req.ops, req.diagram)
    return ApplyEditOpsResponse(
        updated_diagram=updated_diagram,
        updated_xml=export_bpmn_xml(updated_diagram),
        op_results=op_results,
    )


def _build_repair_run(
    config: ExperimentConfig,
    iterations: int,
    converged: bool,
) -> RunBlock:
    return build_run_block(
        config=config,
        model_used=resolve_model(TaskType.REPAIR, config=config),
        converter=converter_version(config),
        rules_version=RULES_VERSION,
        prompt_files={"repair": repair_prompt_path(config)},
        iterations=iterations,
        converged=converged,
    )
