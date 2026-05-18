import pytest

from app.model.protocol import DiagramConverter
from app.model.registry import get_converter
from app.model.schema import BpmnDiagram
from tests.converter_cases import canonical_full_diagram, canonical_xml_supported_diagram


CONVERTER_CASES = [
    pytest.param("pydantic", canonical_xml_supported_diagram, id="pydantic-xml"),
    pytest.param("pydantic_json", canonical_full_diagram, id="pydantic-json"),
]


@pytest.mark.parametrize(("converter_name", "diagram_factory"), CONVERTER_CASES)
def test_converter_preserves_supported_canonical_ir(converter_name, diagram_factory):
    converter = get_converter(converter_name)
    diagram = diagram_factory()

    serialised = converter.serialize(diagram)
    reparsed = converter.parse(serialised)

    assert isinstance(converter, DiagramConverter)
    assert isinstance(reparsed, BpmnDiagram)
    assert reparsed == diagram


@pytest.mark.parametrize(("converter_name", "diagram_factory"), CONVERTER_CASES)
def test_converter_serialization_is_deterministic(converter_name, diagram_factory):
    converter = get_converter(converter_name)
    diagram = diagram_factory()

    assert converter.serialize(diagram) == converter.serialize(diagram)


def test_pydantic_json_registry_alias_matches_primary_converter():
    diagram = canonical_full_diagram()

    primary_payload = get_converter("pydantic_json").serialize(diagram)
    alias_payload = get_converter("pydantic-json").serialize(diagram)

    assert alias_payload == primary_payload
    assert get_converter("pydantic-json").parse(alias_payload) == diagram
