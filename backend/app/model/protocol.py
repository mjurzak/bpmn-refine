"""Converter protocol — the contract every representation format must satisfy.

A DiagramConverter knows how to parse bytes in its external format into the
canonical BPMN diagram model and serialise that model back to bytes.

Adding a new representation (flat JSON, NetworkX graph, ...) means implementing
this protocol and registering it via registry.register().  No other code needs
to change.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.model.schema import BpmnDiagram


@runtime_checkable
class DiagramConverter(Protocol):
    """Stateless converter between external bytes and canonical BPMN IR."""

    def parse(self, payload: bytes) -> BpmnDiagram:
        """Parse bytes and return the canonical diagram model."""
        ...

    def serialize(self, diagram: BpmnDiagram) -> bytes:
        """Serialise the canonical diagram model to external bytes."""
        ...
