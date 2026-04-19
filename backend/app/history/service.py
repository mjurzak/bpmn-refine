"""Persistent diagram revision history.

Each session maps to a workspace directory:
    <workspace_dir>/<session_id>/revisions/<rev_id>.json

rev_id is zero-padded to 4 digits so alphabetical order == chronological order.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from app.model.schema import BpmnDiagram
from app.history.models import Revision, RevisionMeta


def _workspace_root() -> Path:
    from app.core.config import settings
    return Path(settings.workspace_dir)


def _session_dir(session_id: str) -> Path:
    return _workspace_root() / session_id / "revisions"


def _rev_id(index: int) -> str:
    return f"{index:04d}"


def create_session() -> str:
    """create a new session directory and return its ID"""
    session_id = uuid.uuid4().hex
    _session_dir(session_id).mkdir(parents=True, exist_ok=True)
    return session_id


def snapshot(
    session_id: str,
    diagram: BpmnDiagram,
    message: str,
    author: Literal["user", "llm"] = "llm",
) -> Revision:
    """save a new revision and return it"""
    rev_dir = _session_dir(session_id)
    rev_dir.mkdir(parents=True, exist_ok=True)

    # next index = number of existing revision files
    existing = sorted(rev_dir.glob("*.json"))
    index = len(existing)

    rev = Revision(
        rev_id=_rev_id(index),
        session_id=session_id,
        index=index,
        timestamp=datetime.now(tz=timezone.utc),
        author=author,
        message=message,
        diagram=diagram,
    )

    path = rev_dir / f"{rev.rev_id}.json"
    path.write_text(rev.model_dump_json(indent=2), encoding="utf-8")
    return rev


def list_revisions(session_id: str) -> list[RevisionMeta]:
    """return revision metadata in chronological order"""
    rev_dir = _session_dir(session_id)
    if not rev_dir.exists():
        return []

    metas: list[RevisionMeta] = []
    for path in sorted(rev_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        metas.append(RevisionMeta(**{k: v for k, v in data.items() if k != "diagram"}))
    return metas


def get_revision(session_id: str, rev_id: str) -> Revision | None:
    """load a specific revision by rev_id"""
    path = _session_dir(session_id) / f"{rev_id}.json"
    if not path.exists():
        return None
    return Revision.model_validate_json(path.read_text(encoding="utf-8"))


def revert(session_id: str, rev_id: str) -> Revision | None:
    """create a new revision that restores the diagram from rev_id"""
    source = get_revision(session_id, rev_id)
    if source is None:
        return None
    return snapshot(
        session_id=session_id,
        diagram=source.diagram,
        message=f"reverted to revision {rev_id} — {source.message}",
        author="user",
    )
