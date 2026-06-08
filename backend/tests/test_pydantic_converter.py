from pathlib import Path

from lxml import etree

from app.model.formats.pydantic_ir import PydanticConverter


def test_serialize_handles_default_namespace_from_parsed_bpmn():
    converter = PydanticConverter()
    xml_bytes = Path("data/pmo-dataset/bpmn/01.bpmn").read_bytes()

    diagram = converter.parse(xml_bytes)
    serialised = converter.serialize(diagram)
    root = etree.fromstring(serialised)

    assert root.nsmap[None] == "http://www.omg.org/spec/BPMN/20100524/MODEL"
