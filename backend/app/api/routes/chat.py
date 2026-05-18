"""Conversational refinement endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.experiments import ExperimentConfig, RunBlock, build_run_block, converter_version
from app.llm.router import TaskType, resolve_model
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
    config: ExperimentConfig = Field(default_factory=ExperimentConfig)


class ChatResponse(ChatResult):
    run: RunBlock


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse | JSONResponse:
    run = build_run_block(
        config=req.config,
        model_used=resolve_model(TaskType.REFINEMENT, config=req.config),
        converter=converter_version(req.config),
        rules_version=RULES_VERSION,
        prompt_files={"chat": chat_prompt_path()},
    )
    try:
        result = await chat_diagram(
            messages=[
                ServiceChatMessage(**message.model_dump()) for message in req.messages
            ],
            diagram=req.diagram,
            issues=req.issues,
            session_id=req.session_id,
            config=req.config,
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "run": run.model_dump(mode="json")},
        )
    return ChatResponse(**result.model_dump(), run=run)
