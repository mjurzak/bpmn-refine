"""Conversational refinement endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.experiments import ExperimentConfig, RunBlock, build_run_block, converter_version
from app.llm.router import TaskType, resolve_model
from app.llm.tracing import LlmTrace, get_traces, reset_trace_context, start_trace_context
from app.model.schema import BpmnDiagram
from app.services.chat import ChatMessage as ServiceChatMessage
from app.services.chat import ChatResult, chat_diagram, chat_prompt_path
from app.validation.rules import ValidationIssue
from app.validation.rules import RULES_VERSION

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessage(ServiceChatMessage):
    pass


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    diagram: BpmnDiagram | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)
    session_id: str | None = None  # when provided, diagram changes are snapshotted
    snapshot_changes: bool = True
    config: ExperimentConfig = Field(default_factory=ExperimentConfig)


class ChatResponse(ChatResult):
    run: RunBlock
    llm_traces: list[LlmTrace] = Field(default_factory=list)


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse | JSONResponse:
    run = build_run_block(
        config=req.config,
        model_used=resolve_model(TaskType.REFINEMENT, config=req.config),
        converter=converter_version(req.config),
        rules_version=RULES_VERSION,
        prompt_files={"chat": chat_prompt_path()},
    )
    trace_token = start_trace_context()
    try:
        result = await chat_diagram(
            messages=[
                ServiceChatMessage(**message.model_dump()) for message in req.messages
            ],
            diagram=req.diagram,
            issues=req.issues,
            session_id=req.session_id,
            config=req.config,
            snapshot_changes=req.snapshot_changes,
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
    return ChatResponse(**result.model_dump(), run=run, llm_traces=traces)
