"""Diagram upload / retrieval endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from app.model.registry import get_converter
from app.model.schema import BpmnDiagram

router = APIRouter(prefix="/diagrams", tags=["diagrams"])


class DiagramUploadResponse(BaseModel):
    diagram: BpmnDiagram
    message: str = "Diagram parsed successfully"


class DiagramExportResponse(BaseModel):
    xml: str


@router.post("/upload", response_model=DiagramUploadResponse)
async def upload_diagram(file: UploadFile) -> DiagramUploadResponse:
    """Accept a BPMN XML file and return its diagram model."""
    if not file.filename or not file.filename.endswith(".bpmn"):
        raise HTTPException(status_code=400, detail="Only .bpmn files are accepted.")
    content = await file.read()
    try:
        diagram = get_converter().parse(content)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to parse BPMN XML: {exc}") from exc
    return DiagramUploadResponse(diagram=diagram)


@router.post("/export", response_model=DiagramExportResponse)
async def export_diagram(diagram: BpmnDiagram) -> DiagramExportResponse:
    """Convert a diagram model back to BPMN XML."""
    try:
        xml_bytes = get_converter().serialize(diagram)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to serialise diagram: {exc}") from exc
    return DiagramExportResponse(xml=xml_bytes.decode("utf-8"))
