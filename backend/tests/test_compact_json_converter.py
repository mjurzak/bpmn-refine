import json

import pytest

from app.model.formats.compact_json import CompactJsonConverter
from tests.converter_cases import canonical_full_diagram


def test_compact_json_uses_short_keys_and_omits_empty_defaults():
    converter = CompactJsonConverter()

    serialised = converter.serialize(canonical_full_diagram())
    data = json.loads(serialised)

    assert set(data) == {"d", "n", "p", "t"}
    assert "definitions_id" not in serialised.decode("utf-8")
    assert data["p"][0]["v"][1]["t"] == "userTask"
    assert "p" not in data["p"][0]["v"][1]


def test_compact_json_rejects_non_object_payload():
    converter = CompactJsonConverter()

    with pytest.raises(TypeError):
        converter.parse(b"[]")
