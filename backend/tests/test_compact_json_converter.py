import json

import pytest

from app.model.formats.compact_json import CompactJsonConverter
from app.model.schema import EventDefinition, EventDefinitionType
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


def test_compact_json_preserves_event_definitions():
    converter = CompactJsonConverter()
    diagram = canonical_full_diagram()
    diagram.processes[0].flow_nodes[0].event_definitions = [
        EventDefinition(
            type=EventDefinitionType.MESSAGE,
            id="message_def_1",
            extra={"messageRef": "Message_1"},
        )
    ]

    payload = converter.serialize(diagram)

    assert converter.parse(payload) == diagram
    assert json.loads(payload)["p"][0]["v"][0]["d"] == [
        {"i": "message_def_1", "t": "messageEventDefinition", "x": {"messageRef": "Message_1"}}
    ]
