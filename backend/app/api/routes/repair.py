"""Repair proposal endpoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.experiments import (
    ExperimentConfig,
    RunBlock,
    build_run_block,
    converter_version,
)
from app.llm.router import TaskType, resolve_model
from app.llm.tracing import (
    LlmTrace,
    get_traces,
    models_called,
    reset_trace_context,
    start_trace_context,
)
from app.model.schema import BpmnDiagram
from app.repair.ops import EditOp, EditOpResult, apply_edit_ops
from app.services.diagrams import export_bpmn_xml, parse_bpmn_bytes
from app.services.repair import (
    OpOrigin,
    StopReason,
    dispatch_repair,
    repair_diagram,
    repair_prompt_path,
    repair_raw_xml,
    xml_repair_prompt_path,
)
from app.services.validation import validate_prompt_path
from app.validation.checkers import checker_versions
from app.validation.rules import RULES_VERSION, ValidationIssue

router = APIRouter(prefix="/repair", tags=["repair"])


class RepairRequest(BaseModel):
    xml: str
    issues: list[ValidationIssue] = Field(default_factory=list)
    config: ExperimentConfig = Field(default_factory=ExperimentConfig)
    single_plan: bool = Field(
        default=True,
        description=(
            "Return one human-reviewable plan for the highest-priority current "
            "findings: all errors, or warnings only when no errors remain. Set "
            "false only for an explicitly requested closed-loop auto-repair."
        ),
    )


class RepairResponse(BaseModel):
    input_diagram: BpmnDiagram
    updated_xml: str
    updated_diagram: BpmnDiagram
    applied_ops: list[EditOp] = Field(default_factory=list)
    applied_op_origins: list[OpOrigin] = Field(default_factory=list)
    failed_ops: list[EditOpResult] = Field(default_factory=list)
    failed_op_origins: list[OpOrigin] = Field(default_factory=list)
    remaining_issues: list[ValidationIssue] = Field(default_factory=list)
    iterations: int
    converged: bool
    errors_resolved: bool = False
    stop_reason: StopReason = StopReason.ITERATION_BUDGET
    single_plan: bool = True
    run: RunBlock
    llm_traces: list[LlmTrace] = Field(default_factory=list)


class RepairXmlRequest(BaseModel):
    xml: str
    instruction: str | None = None
    config: ExperimentConfig = Field(default_factory=ExperimentConfig)


class RepairXmlResponse(BaseModel):
    updated_xml: str
    parseable: bool
    diagram: BpmnDiagram | None = None
    parse_error: str | None = None
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
            single_plan=req.single_plan,
        )
        updated_xml = export_bpmn_xml(repair_result.repaired_diagram)
    except Exception as exc:
        traces = get_traces()
        reset_trace_context(trace_token)
        run = _build_repair_run(
            req.config, iterations=iterations, converged=converged, traces=traces
        )
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
    traces = get_traces()
    run = _build_repair_run(
        req.config, iterations=iterations, converged=converged, traces=traces
    )
    reset_trace_context(trace_token)

    return RepairResponse(
        input_diagram=diagram,
        updated_xml=updated_xml,
        updated_diagram=repair_result.repaired_diagram,
        applied_ops=repair_result.applied_ops,
        applied_op_origins=repair_result.applied_op_origins,
        failed_ops=repair_result.failed_ops,
        failed_op_origins=repair_result.failed_op_origins,
        remaining_issues=remaining_issues,
        iterations=iterations,
        converged=converged,
        errors_resolved=repair_result.errors_resolved,
        stop_reason=repair_result.stop_reason,
        single_plan=req.single_plan,
        run=run,
        llm_traces=traces,
    )


@router.post("/xml", response_model=RepairXmlResponse)
async def repair_xml(req: RepairXmlRequest) -> RepairXmlResponse | JSONResponse:
    """free-form repair for XML that cannot be parsed into the IR

    the model rewrites the raw BPMN XML directly (e.g. to dedupe element IDs).
    the result is re-parsed: on success the canonical diagram is returned so the
    UI can re-enable the structured flows; otherwise only the corrected XML is.
    """
    xml_prompt_files = {"repair_xml": xml_repair_prompt_path()}
    run = _build_repair_run(
        req.config, iterations=1, converged=False, prompt_files=xml_prompt_files
    )
    trace_token = start_trace_context()
    try:
        updated_xml = await repair_raw_xml(
            req.xml,
            instruction=req.instruction,
            config=req.config,
        )
    except Exception as exc:
        traces = get_traces()
        reset_trace_context(trace_token)
        run = _build_repair_run(
            req.config,
            iterations=1,
            converged=False,
            prompt_files=xml_prompt_files,
            traces=traces,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": str(exc),
                "run": run.model_dump(mode="json"),
                "llm_traces": [trace.model_dump(mode="json") for trace in traces],
            },
        )

    parseable = True
    diagram: BpmnDiagram | None = None
    parse_error: str | None = None
    try:
        diagram = parse_bpmn_bytes(updated_xml.encode("utf-8"))
    except Exception as exc:
        parseable = False
        parse_error = str(exc)

    traces = get_traces()
    run = _build_repair_run(
        req.config,
        iterations=1,
        converged=parseable,
        prompt_files=xml_prompt_files,
        traces=traces,
    )
    reset_trace_context(trace_token)
    return RepairXmlResponse(
        updated_xml=updated_xml,
        parseable=parseable,
        diagram=diagram,
        parse_error=parse_error,
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
    prompt_files: dict[str, Path] | None = None,
    traces: list[LlmTrace] | None = None,
) -> RunBlock:
    """describe the repair run, including what its nested revalidation invoked

    Each iteration revalidates, so tier 3 also runs the semantic prompt and tier
    2 the formal checkers; recording only the repair prompt understated the run.
    `model_used` comes from the traces, not the router, because an all-quick-fix
    repair reaches no provider at all.
    """
    resolved_prompts = prompt_files or {"repair": repair_prompt_path(config)}
    if prompt_files is None and config.tiers_enabled.t3:
        resolved_prompts = {**resolved_prompts, "validate": validate_prompt_path()}
    return build_run_block(
        config=config,
        model_used=models_called(traces or []),
        model_configured=resolve_model(TaskType.REPAIR, config=config),
        converter=converter_version(config),
        rules_version=RULES_VERSION,
        prompt_files=resolved_prompts,
        checkers=checker_versions(config) if config.tiers_enabled.t2 else None,
        iterations=iterations,
        converged=converged,
    )
