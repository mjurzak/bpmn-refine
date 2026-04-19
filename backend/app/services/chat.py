"""Reusable conversational refinement orchestration for API routes and CLI commands."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel

from app.history import service as hist
from app.llm import client as llm_client
from app.llm.router import TaskType, resolve_model, resolve_provider
from app.model.schema import BpmnDiagram
from app.validation.rules import ValidationIssue

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent.parent / "llm" / "prompts"
_CHAT_PROMPT = _PROMPT_DIR / "chat_system.txt"


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatResult(BaseModel):
    reply: str
    updated_diagram: BpmnDiagram | None = None
    rev_id: str | None = None
    session_id: str | None = None


async def chat_diagram(
    messages: list[ChatMessage],
    diagram: BpmnDiagram | None = None,
    issues: list[ValidationIssue] | None = None,
    session_id: str | None = None,
) -> ChatResult:
    """run one conversational refinement turn and optionally snapshot diagram output"""
    system_prompt = _CHAT_PROMPT.read_text()
    issues = issues or []

    context_prefix = ""
    if diagram:
        context_prefix += (
            f"Current diagram:\n```json\n{diagram.model_dump_json(indent=2)}\n```\n\n"
        )
    if issues:
        issues_json = json.dumps([issue.__dict__ for issue in issues], indent=2)
        context_prefix += f"Current validation issues:\n```json\n{issues_json}\n```\n\n"

    payload_messages: list[dict[str, str]] = []
    for index, message in enumerate(messages):
        content = message.content
        if index == 0 and context_prefix:
            content = context_prefix + content
        payload_messages.append({"role": message.role, "content": content})

    reply = await llm_client.complete_with_history(
        messages=payload_messages,
        system=system_prompt,
        model=resolve_model(TaskType.REFINEMENT),
        provider=resolve_provider(TaskType.REFINEMENT),
    )

    updated_diagram = _parse_diagram_from_reply(reply)
    rev_id: str | None = None
    new_session_id: str | None = None

    if updated_diagram is not None:
        active_session_id = session_id
        if not active_session_id:
            active_session_id = hist.create_session()
            new_session_id = active_session_id

        first_line = (
            reply.strip().splitlines()[0][:120] if reply.strip() else "llm update"
        )
        try:
            revision = hist.snapshot(
                session_id=active_session_id,
                diagram=updated_diagram,
                message=first_line,
                author="llm",
            )
            rev_id = revision.rev_id
        except Exception as exc:
            logger.warning("failed to snapshot diagram revision: %s", exc)

    return ChatResult(
        reply=reply,
        updated_diagram=updated_diagram,
        rev_id=rev_id,
        session_id=new_session_id,
    )


def chat_prompt_name() -> str:
    return _CHAT_PROMPT.name


def _parse_diagram_from_reply(reply: str) -> BpmnDiagram | None:
    # accept whichever fence name the LLM chose
    for fence in ("```diagram", "```ir", "```json"):
        if fence not in reply:
            continue
        try:
            start = reply.index(fence) + len(fence)
            end = reply.index("```", start)
            diagram_json = reply[start:end].strip()
            logger.info("parsed updated diagram from %s fence", fence)
            return BpmnDiagram.model_validate_json(diagram_json)
        except Exception as exc:
            logger.warning("failed to parse diagram from %s fence: %s", fence, exc)
    return None
