"""Reusable BPMN diagram loading and export helpers."""

from __future__ import annotations

from pathlib import Path

from app.model.protocol import UnsupportedElement
from app.model.registry import get_converter
from app.model.schema import BpmnDiagram


def parse_bpmn_bytes(xml_bytes: bytes) -> BpmnDiagram:
    """parse BPMN XML bytes into the canonical diagram model"""
    return get_converter().parse(xml_bytes)


def parse_bpmn_bytes_with_diagnostics(
    xml_bytes: bytes,
) -> tuple[BpmnDiagram, list[UnsupportedElement]]:
    """parse BPMN XML, also reporting children the IR does not represent"""
    return get_converter().parse_with_diagnostics(xml_bytes)


def describe_unsupported(elements: list[UnsupportedElement]) -> str | None:
    """one human-readable sentence naming what the import left behind"""
    if not elements:
        return None
    listed = ", ".join(element.describe() for element in elements)
    return (
        f"{len(elements)} element(s) were not imported because the intermediate "
        f"representation does not cover them yet: {listed}. They are absent from "
        "the model, from validation, and from any exported XML."
    )


def load_bpmn_file(path: Path) -> BpmnDiagram:
    """load a BPMN file from disk and parse it"""
    return parse_bpmn_bytes(path.read_bytes())


def export_bpmn_xml(diagram: BpmnDiagram) -> str:
    """serialise the canonical diagram model back to BPMN XML"""
    return get_converter().serialize(diagram).decode("utf-8")
