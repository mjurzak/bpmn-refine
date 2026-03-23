"""Converter protocol — the contract every representation format must satisfy.

A DiagramConverter knows how to:
  - parse raw BPMN XML into its own diagram type
  - serialise that type back to valid BPMN XML

Adding a new representation (flat JSON, NetworkX graph, …) means implementing
this protocol and registering it via registry.register().  No other code needs
to change.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DiagramConverter(Protocol):
    """Stateless converter between BPMN XML and an internal diagram model."""

    def parse(self, xml_bytes: bytes) -> Any:
        """Parse BPMN XML and return a diagram object in this format."""
        ...

    def serialize(self, diagram: Any) -> bytes:
        """Serialise a diagram object back to BPMN XML bytes."""
        ...
