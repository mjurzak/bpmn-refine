"""Conversational refinement endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.model.schema import BpmnDiagram
from app.services.chat import ChatMessage as ServiceChatMessage
from app.services.chat import ChatResult, chat_diagram
from app.validation.rules import ValidationIssue

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessage(ServiceChatMessage):
    pass


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    diagram: BpmnDiagram | None = None
    issues: list[ValidationIssue] = []
    session_id: str | None = None  # when provided, diagram changes are snapshotted


class ChatResponse(ChatResult):
    pass


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    result = await chat_diagram(
        messages=[
            ServiceChatMessage(**message.model_dump()) for message in req.messages
        ],
        diagram=req.diagram,
        issues=req.issues,
        session_id=req.session_id,
    )
    return ChatResponse(**result.model_dump())
