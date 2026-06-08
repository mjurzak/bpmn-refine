import json

import pytest

from app.model.formats.pydantic_json import PydanticJsonConverter
from tests.converter_cases import canonical_full_diagram


def test_pydantic_json_serializes_deterministic_compact_json():
    converter = PydanticJsonConverter()

    serialised = converter.serialize(canonical_full_diagram())

    assert b": " not in serialised
    assert b", " not in serialised
    assert list(json.loads(serialised).keys()) == [
        "definitions_id",
        "namespaces",
        "processes",
        "target_namespace",
    ]


def test_pydantic_json_rejects_malformed_payload():
    converter = PydanticJsonConverter()

    with pytest.raises(ValueError):
        converter.parse(b'{"definitions_id":"definitions_1"')
