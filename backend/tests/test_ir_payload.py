import json

from app.experiments import ExperimentConfig, IrFormat, converter_version
from app.services.ir_payload import (
    diagram_fence,
    diagram_payload,
    diagram_payload_text,
    parse_diagram_from_fenced_reply,
    parse_diagram_payload,
    strip_diagram_from_fenced_reply,
)
from tests.converter_cases import canonical_full_diagram


def test_default_pydantic_payload_stays_json_object():
    diagram = canonical_full_diagram()
    config = ExperimentConfig()

    payload = diagram_payload(diagram, config)

    assert isinstance(payload, dict)
    assert payload["definitions_id"] == "definitions_1"
    assert parse_diagram_payload(payload, config) == diagram
    assert diagram_fence(config) == "json"
    assert converter_version(config) == "pydantic_ir@v1"


def test_yaml_payload_uses_selected_converter():
    diagram = canonical_full_diagram()
    config = ExperimentConfig(ir_format=IrFormat.YAML)

    payload = diagram_payload(diagram, config)

    assert isinstance(payload, str)
    assert "definitions_id: definitions_1" in payload
    assert parse_diagram_payload(payload, config) == diagram
    assert diagram_fence(config) == "yaml"
    assert converter_version(config) == "yaml@v1"


def test_compact_json_payload_uses_selected_converter():
    diagram = canonical_full_diagram()
    config = ExperimentConfig(ir_format=IrFormat.COMPACT_JSON)

    payload = diagram_payload(diagram, config)
    data = json.loads(payload)

    assert isinstance(payload, str)
    assert "definitions_id" not in payload
    assert data["d"] == "definitions_1"
    assert parse_diagram_payload(payload, config) == diagram
    assert diagram_fence(config) == "json"


def test_fenced_reply_parser_uses_selected_mermaid_converter():
    diagram = canonical_full_diagram()
    config = ExperimentConfig(ir_format=IrFormat.MERMAID)
    payload = diagram_payload_text(diagram, config)

    parsed = parse_diagram_from_fenced_reply(
        f"Here is the updated diagram:\n```mermaid\n{payload}```",
        config,
    )

    assert parsed == diagram


def test_strip_diagram_from_fenced_reply_hides_pydantic_payload():
    reply = (
        "I added a recovery path.\n\n"
        "```pydantic\n{\"definitions_id\": \"definitions_1\"}\n```"
    )

    visible_reply = strip_diagram_from_fenced_reply(reply, ExperimentConfig())

    assert visible_reply == "I added a recovery path."


def test_strip_diagram_from_fenced_reply_preserves_unrelated_code():
    reply = "Use this expression:\n```python\npayment_successful = True\n```"

    visible_reply = strip_diagram_from_fenced_reply(reply, ExperimentConfig())

    assert visible_reply == reply
