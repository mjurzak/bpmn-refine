"""Canonical Pydantic BPMN IR serialised as deterministic JSON."""

from __future__ import annotations

import json

from app.model.protocol import BaseDiagramConverter
from app.model.schema import BpmnDiagram


class PydanticJsonConverter(BaseDiagramConverter):
    """Converts between UTF-8 JSON bytes and the canonical BpmnDiagram model."""

    def parse(self, payload: bytes) -> BpmnDiagram:
        """Parse canonical IR JSON bytes into a BpmnDiagram."""
        return BpmnDiagram.model_validate_json(payload)

    def serialize(self, diagram: BpmnDiagram) -> bytes:
        """Serialise a BpmnDiagram into stable, compact JSON bytes."""
        data = diagram.model_dump(mode="json")
        return json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
