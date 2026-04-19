from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.model.schema import BpmnDiagram


class Revision(BaseModel):
    """metadata + snapshot of a diagram at a point in time"""

    rev_id: str                          # zero-padded index, e.g. "0000"
    session_id: str
    index: int                           # 0-based integer revision number
    timestamp: datetime
    author: Literal["user", "llm"]       # who produced this revision
    message: str                         # short description of the change
    diagram: BpmnDiagram


class RevisionMeta(BaseModel):
    """lightweight summary used in list responses (no full diagram)"""

    rev_id: str
    session_id: str
    index: int
    timestamp: datetime
    author: Literal["user", "llm"]
    message: str


class HistoryResponse(BaseModel):
    session_id: str
    revisions: list[RevisionMeta]


class RevertResponse(BaseModel):
    session_id: str
    new_rev_id: str
    diagram: BpmnDiagram
