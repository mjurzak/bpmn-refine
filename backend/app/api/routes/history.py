from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.history import service as hist
from app.history.models import HistoryResponse, Revision, RevertResponse
from app.model.schema import BpmnDiagram

router = APIRouter(prefix="/history", tags=["history"])


class CommitRevisionRequest(BaseModel):
    diagram: BpmnDiagram
    session_id: str | None = None
    message: str = "accepted proposal"
    author: Literal["user", "llm"] = "llm"


class CommitRevisionResponse(BaseModel):
    session_id: str
    new_rev_id: str
    diagram: BpmnDiagram


@router.post("/commit", response_model=CommitRevisionResponse)
def commit_revision(req: CommitRevisionRequest):
    """commit an explicitly accepted proposal into session history"""
    session_id = req.session_id or hist.create_session()
    revision = hist.snapshot(
        session_id=session_id,
        diagram=req.diagram,
        message=req.message,
        author=req.author,
    )
    return CommitRevisionResponse(
        session_id=session_id,
        new_rev_id=revision.rev_id,
        diagram=revision.diagram,
    )


@router.get("/{session_id}", response_model=HistoryResponse)
def get_history(session_id: str):
    """list all revisions for a session in chronological order"""
    revisions = hist.list_revisions(session_id)
    return HistoryResponse(session_id=session_id, revisions=revisions)


@router.get("/{session_id}/{rev_id}", response_model=Revision)
def get_revision(session_id: str, rev_id: str):
    """get a specific revision including its full diagram"""
    rev = hist.get_revision(session_id, rev_id)
    if rev is None:
        raise HTTPException(status_code=404, detail=f"revision {rev_id!r} not found")
    return rev


@router.post("/{session_id}/revert/{rev_id}", response_model=RevertResponse)
def revert_to_revision(session_id: str, rev_id: str):
    """create a new revision that restores the diagram to rev_id"""
    new_rev = hist.revert(session_id, rev_id)
    if new_rev is None:
        raise HTTPException(status_code=404, detail=f"revision {rev_id!r} not found")
    return RevertResponse(
        session_id=session_id,
        new_rev_id=new_rev.rev_id,
        diagram=new_rev.diagram,
    )
