"""Reusable conversational refinement orchestration for API routes and CLI commands."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from app.experiments import ExperimentConfig
from app.history import service as hist
from app.llm import client as llm_client
from app.llm.envelope import LlmResponseEnvelope
from app.llm.prompt_context import render_prompt_template
from app.llm.router import TaskType, resolve_model, resolve_provider, resolve_sampling
from app.llm.schema import strict_json_schema
from app.model.schema import BpmnDiagram
from app.services.ir_payload import (
    call_with_ir_correction,
    diagram_fence,
    diagram_payload_text,
    parse_diagram_payload,
)
from app.validation.rules import ValidationIssue, issue_to_dict

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


class ChatMachineResult(BaseModel):
    diagram: str | None = Field(
        default=None,
        description=(
            "Complete updated diagram in the requested IR format, or null when "
            "the response proposes no diagram change."
        ),
    )


ChatResponseEnvelope = LlmResponseEnvelope[ChatMachineResult]
_CHAT_SCHEMA = strict_json_schema(ChatResponseEnvelope)


def _best_effort_description(parsed: object) -> str:
    """the model's prose, before the envelope has been validated

    A rejected envelope still usually carries something a human can read.
    """
    if not isinstance(parsed, dict):
        return ""
    description = parsed.get("description")
    return description.strip() if isinstance(description, str) else ""


async def chat_diagram(
    messages: list[ChatMessage],
    diagram: BpmnDiagram | None = None,
    issues: list[ValidationIssue] | None = None,
    session_id: str | None = None,
    config: ExperimentConfig | None = None,
    snapshot_changes: bool = True,
) -> ChatResult:
    """run one conversational refinement turn and optionally snapshot diagram output"""
    system_prompt = render_prompt_template(_CHAT_PROMPT, config=config)
    issues = issues or []

    context_prefix = ""
    if diagram:
        fence = diagram_fence(config)
        diagram_text = diagram_payload_text(diagram, config)
        context_prefix += (
            f"Current diagram ({(config or ExperimentConfig()).ir_format}):\n"
            f"```{fence}\n{diagram_text}\n```\n\n"
        )
    if issues:
        issues_json = json.dumps(
            [
                issue_to_dict(
                    issue,
                    include_formal_evidence=(
                        config or ExperimentConfig()
                    ).include_formal_evidence,
                )
                for issue in issues
            ],
            indent=2,
        )
        context_prefix += f"Current validation issues:\n```json\n{issues_json}\n```\n\n"

    payload_messages: list[dict[str, str]] = []
    for index, message in enumerate(messages):
        content = message.content
        if index == 0 and context_prefix:
            content = context_prefix + content
        payload_messages.append({"role": message.role, "content": content})

    last_reply = ""

    async def attempt(feedback: str | None) -> tuple[str, BpmnDiagram | None]:
        nonlocal last_reply
        turns = list(payload_messages)
        if feedback:
            turns.append({"role": "user", "content": feedback})
        parsed = await llm_client.complete_structured_with_history(
            messages=turns,
            schema=_CHAT_SCHEMA,
            task=TaskType.REFINEMENT,
            system=system_prompt,
            model=resolve_model(TaskType.REFINEMENT, config=config),
            provider=resolve_provider(TaskType.REFINEMENT, config=config),
            reasoning_effort=str(config.reasoning_effort)
            if config and config.reasoning_effort
            else None,
            **resolve_sampling(config),
        )
        # captured before validation: the fallback below promises the user the
        # model's text even when the envelope is rejected, and assigning only
        # after model_validate would leave that promise unfulfilled
        last_reply = _best_effort_description(parsed) or last_reply
        response = ChatResponseEnvelope.model_validate(parsed)
        last_reply = response.description
        updated = (
            parse_diagram_payload(response.result.diagram, config)
            if response.result.diagram is not None
            else None
        )
        return last_reply, updated

    try:
        reply, updated_diagram = await call_with_ir_correction(attempt)
    except Exception as exc:
        # corrections exhausted. a chat turn must not hard-fail over a bad diagram —
        # the user still gets the model's text, just without an applicable change
        logger.warning("failed to parse diagram from reply: %s", exc)
        reply, updated_diagram = last_reply, None
    rev_id: str | None = None
    new_session_id: str | None = None

    if updated_diagram is not None and snapshot_changes:
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


def chat_prompt_path() -> Path:
    return _CHAT_PROMPT
