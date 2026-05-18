"""Canonical Pydantic BPMN IR serialised as YAML."""

from __future__ import annotations

import yaml

from app.model.schema import BpmnDiagram


class YamlConverter:
    """Converts between UTF-8 YAML bytes and the canonical BpmnDiagram model."""

    def parse(self, payload: bytes) -> BpmnDiagram:
        """Parse canonical IR YAML bytes into a BpmnDiagram."""
        data = yaml.safe_load(payload.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("YAML diagram payload must be a mapping")
        return BpmnDiagram.model_validate(data)

    def serialize(self, diagram: BpmnDiagram) -> bytes:
        """Serialise a BpmnDiagram into deterministic YAML bytes."""
        data = diagram.model_dump(mode="json")
        return yaml.safe_dump(
            data,
            allow_unicode=True,
            sort_keys=True,
        ).encode("utf-8")
