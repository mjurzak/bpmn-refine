"""Reusable repair orchestration for CLI and future API routes."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from app.experiments import ExperimentConfig
from app.history import service as hist
from app.llm import client as llm_client
from app.llm.router import TaskType, resolve_model, resolve_provider
from app.model.schema import BpmnDiagram
from app.validation.rules import ValidationIssue

_PROMPT_DIR = Path(__file__).parent.parent / "llm" / "prompts"
_REPAIR_PROMPT = _PROMPT_DIR / "repair.txt"


class UnresolvedRepair(BaseModel):
    rule_id: str | None = None
    reason: str


class RepairResult(BaseModel):
    repaired_diagram: BpmnDiagram
    unresolved: list[UnresolvedRepair] = []
    rev_id: str | None = None
    session_id: str | None = None


async def repair_diagram(
    diagram: BpmnDiagram,
    issues: list[ValidationIssue],
    session_id: str | None = None,
    config: ExperimentConfig | None = None,
    snapshot: bool = True,
) -> RepairResult:
    """repair a diagram using the existing repair prompt and issue list"""
    payload = {
        "diagram": diagram.model_dump(),
        "issues": [issue.__dict__ for issue in issues],
    }
    raw = await llm_client.complete(
        prompt=json.dumps(payload),
        system=_REPAIR_PROMPT.read_text(),
        model=resolve_model(TaskType.REPAIR, config=config),
        provider=resolve_provider(TaskType.REPAIR, config=config),
    )
    parsed = json.loads(raw)
    repaired_diagram = BpmnDiagram.model_validate(parsed["ir"])
    unresolved = [_normalise_unresolved(item) for item in parsed.get("unresolved", [])]

    if not snapshot:
        return RepairResult(repaired_diagram=repaired_diagram, unresolved=unresolved)

    active_session_id = session_id
    new_session_id: str | None = None
    if active_session_id is None:
        active_session_id = hist.create_session()
        new_session_id = active_session_id

    revision = hist.snapshot(
        session_id=active_session_id,
        diagram=repaired_diagram,
        message="llm repair",
        author="llm",
    )

    return RepairResult(
        repaired_diagram=repaired_diagram,
        unresolved=unresolved,
        rev_id=revision.rev_id,
        session_id=new_session_id,
    )


def repair_prompt_name() -> str:
    return _REPAIR_PROMPT.name


def repair_prompt_path() -> Path:
    return _REPAIR_PROMPT


def _normalise_unresolved(item: object) -> UnresolvedRepair:
    if isinstance(item, str):
        return UnresolvedRepair(reason=item)
    if isinstance(item, dict):
        return UnresolvedRepair(
            rule_id=item.get("rule_id") or item.get("id"),
            reason=item.get("reason") or item.get("message") or json.dumps(item),
        )
    return UnresolvedRepair(reason=str(item))
