"""Reusable BPMN diagram loading and export helpers."""

from __future__ import annotations

from pathlib import Path

from app.model.registry import get_converter
from app.model.schema import BpmnDiagram


def parse_bpmn_bytes(xml_bytes: bytes) -> BpmnDiagram:
    """parse BPMN XML bytes into the canonical diagram model"""
    return get_converter().parse(xml_bytes)


def load_bpmn_file(path: Path) -> BpmnDiagram:
    """load a BPMN file from disk and parse it"""
    return parse_bpmn_bytes(path.read_bytes())


def export_bpmn_xml(diagram: BpmnDiagram) -> str:
    """serialise the canonical diagram model back to BPMN XML"""
    return get_converter().serialize(diagram).decode("utf-8")
