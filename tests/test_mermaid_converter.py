import pytest

from app.model.formats.mermaid import MermaidConverter
from tests.converter_cases import canonical_full_diagram


def test_mermaid_serializes_flowchart_with_metadata():
    converter = MermaidConverter()

    serialised = converter.serialize(canonical_full_diagram()).decode("utf-8")

    assert serialised.startswith("%% bpmn-ai-mermaid:v1\n")
    assert "%% bpmn-ai-ir:" in serialised
    assert "flowchart TD" in serialised
    assert "p0n0((Start))" in serialised
    assert "p0n0 --> p0n1" in serialised


def test_mermaid_rejects_payload_without_metadata():
    converter = MermaidConverter()

    with pytest.raises(ValueError):
        converter.parse(b"flowchart TD\n  a --> b\n")
