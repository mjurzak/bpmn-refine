"""Diagram upload / retrieval endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.history import service as hist
from app.model.schema import BpmnDiagram
from app.services.diagrams import (
    describe_unsupported,
    export_bpmn_xml,
    parse_bpmn_bytes_with_diagnostics,
)

router = APIRouter(prefix="/diagrams", tags=["diagrams"])


class UnsupportedElementReport(BaseModel):
    """a BPMN child the import read past because the IR cannot hold it"""

    tag: str
    scope: str
    element_id: str | None = None
    parent_id: str | None = None


class DiagramUploadResponse(BaseModel):
    diagram: BpmnDiagram
    session_id: str
    message: str = "Diagram parsed successfully"
    unsupported_elements: list[UnsupportedElementReport] = Field(default_factory=list)
    unsupported_warning: str | None = None


class DiagramExportResponse(BaseModel):
    xml: str


class DiagramParseRequest(BaseModel):
    xml: str


class DiagramParseResponse(BaseModel):
    diagram: BpmnDiagram
    message: str = "Diagram parsed successfully"
    unsupported_elements: list[UnsupportedElementReport] = Field(default_factory=list)
    unsupported_warning: str | None = None


@router.post("/upload", response_model=DiagramUploadResponse)
async def upload_diagram(file: UploadFile) -> DiagramUploadResponse:
    """Accept a BPMN XML file, return its diagram model and a new session ID."""
    if not file.filename or not file.filename.endswith(".bpmn"):
        raise HTTPException(status_code=400, detail="Only .bpmn files are accepted.")
    content = await file.read()
    try:
        diagram, unsupported = parse_bpmn_bytes_with_diagnostics(content)
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

    return DiagramUploadResponse(
        diagram=diagram,
        session_id=session_id,
        unsupported_elements=[
            UnsupportedElementReport(**vars(element)) for element in unsupported
        ],
        unsupported_warning=describe_unsupported(unsupported),
    )


@router.post("/parse", response_model=DiagramParseResponse)
async def parse_diagram(req: DiagramParseRequest) -> DiagramParseResponse:
    """Convert BPMN XML into the canonical diagram model without snapshotting."""
    try:
        diagram, unsupported = parse_bpmn_bytes_with_diagnostics(
            req.xml.encode("utf-8")
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"Failed to parse BPMN XML: {exc}"
        ) from exc
    return DiagramParseResponse(
        diagram=diagram,
        unsupported_elements=[
            UnsupportedElementReport(**vars(element)) for element in unsupported
        ],
        unsupported_warning=describe_unsupported(unsupported),
    )


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
