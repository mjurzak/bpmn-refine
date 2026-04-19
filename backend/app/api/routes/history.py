from fastapi import APIRouter, HTTPException

from app.history import service as hist
from app.history.models import HistoryResponse, Revision, RevertResponse

router = APIRouter(prefix="/history", tags=["history"])


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
