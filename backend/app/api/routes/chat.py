"""Conversational refinement endpoint."""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter
from pydantic import BaseModel

from app.model.schema import BpmnDiagram
from app.llm import client as llm_client
from app.llm.router import TaskType, resolve_model, resolve_provider
from app.validation.rules import ValidationIssue

router = APIRouter(prefix="/chat", tags=["chat"])

_PROMPT_DIR = Path(__file__).parent.parent.parent / "llm" / "prompts"


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    diagram: BpmnDiagram | None = None
    issues: list[ValidationIssue] = []


class ChatResponse(BaseModel):
    reply: str
    # non-null when the LLM produced an updated diagram model
    updated_diagram: BpmnDiagram | None = None


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    system_prompt = (_PROMPT_DIR / "chat_system.txt").read_text()

    # prepend diagram and issues context as the first user message if provided
    context_prefix = ""
    if req.diagram:
        context_prefix += f"Current diagram:\n```json\n{req.diagram.model_dump_json(indent=2)}\n```\n\n"
    if req.issues:
        issues_json = json.dumps([i.__dict__ for i in req.issues], indent=2)
        context_prefix += f"Current validation issues:\n```json\n{issues_json}\n```\n\n"

    messages: list[dict] = []
    for i, msg in enumerate(req.messages):
        content = msg.content
        if i == 0 and context_prefix:
            content = context_prefix + content
        messages.append({"role": msg.role, "content": content})

    reply = await llm_client.complete_with_history(
        messages=messages,
        system=system_prompt,
        model=resolve_model(TaskType.REFINEMENT),
        provider=resolve_provider(TaskType.REFINEMENT),
    )

    updated_diagram: BpmnDiagram | None = None
    # extract diagram update if the assistant wrapped one in ```diagram ... ```
    if "```diagram" in reply:
        try:
            start = reply.index("```diagram") + 10
            end = reply.index("```", start)
            diagram_json = reply[start:end].strip()
            updated_diagram = BpmnDiagram.model_validate_json(diagram_json)
        except Exception as exc:
            logger.warning("failed to parse diagram from LLM reply: %s", exc)

    return ChatResponse(reply=reply, updated_diagram=updated_diagram)
