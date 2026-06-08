import pytest
import yaml

from app.model.formats.yaml_ir import YamlConverter
from tests.converter_cases import canonical_full_diagram


def test_yaml_serializes_readable_mapping():
    converter = YamlConverter()

    serialised = converter.serialize(canonical_full_diagram())
    data = yaml.safe_load(serialised.decode("utf-8"))

    assert data["definitions_id"] == "definitions_1"
    assert data["processes"][0]["flow_nodes"][1]["type"] == "userTask"


def test_yaml_rejects_non_mapping_payload():
    converter = YamlConverter()

    with pytest.raises(ValueError):
        converter.parse(b"- not\n- a\n- diagram\n")
