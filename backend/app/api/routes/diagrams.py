"""Diagram upload / retrieval endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from app.model.schema import BpmnDiagram
from app.history import service as hist
from app.services.diagrams import export_bpmn_xml, parse_bpmn_bytes

router = APIRouter(prefix="/diagrams", tags=["diagrams"])


class DiagramUploadResponse(BaseModel):
    diagram: BpmnDiagram
    session_id: str
    message: str = "Diagram parsed successfully"


class DiagramExportResponse(BaseModel):
    xml: str


@router.post("/upload", response_model=DiagramUploadResponse)
async def upload_diagram(file: UploadFile) -> DiagramUploadResponse:
    """Accept a BPMN XML file, return its diagram model and a new session ID."""
    if not file.filename or not file.filename.endswith(".bpmn"):
        raise HTTPException(status_code=400, detail="Only .bpmn files are accepted.")
    content = await file.read()
    try:
        diagram = parse_bpmn_bytes(content)
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"Failed to parse BPMN XML: {exc}"
        ) from exc

    session_id = hist.create_session()
    hist.snapshot(
        session_id=session_id,
        diagram=diagram,
        message=f"uploaded {file.filename}",
        author="user",
    )

    return DiagramUploadResponse(diagram=diagram, session_id=session_id)


@router.post("/export", response_model=DiagramExportResponse)
async def export_diagram(diagram: BpmnDiagram) -> DiagramExportResponse:
    """Convert a diagram model back to BPMN XML."""
    try:
        xml = export_bpmn_xml(diagram)
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"Failed to serialise diagram: {exc}"
        ) from exc
    return DiagramExportResponse(xml=xml)
