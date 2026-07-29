"""Converter protocol — the contract every representation format must satisfy.

A DiagramConverter knows how to parse bytes in its external format into the
canonical BPMN diagram model and serialise that model back to bytes.

Adding a new representation (flat JSON, NetworkX graph, ...) means implementing
this protocol and registering it via registry.register().  No other code needs
to change.

Parsing has two entry points.  `parse` returns the model alone and keeps the
round-trip property readable: parse(serialize(d)) == d.  `parse_with_diagnostics`
additionally reports the parts of the *input document* the IR does not
represent; the diagnostics stay off `BpmnDiagram` on purpose, since a re-parse of
exported bytes would otherwise compare unequal to the model it came from.

Most formats serialise the IR itself and so have nothing to leave behind —
BaseDiagramConverter gives them the empty-diagnostics default, and only a
converter reading a richer external vocabulary (BPMN XML) overrides it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.model.schema import BpmnDiagram


@dataclass(frozen=True)
class UnsupportedElement:
    """one input child the converter read past without representing it"""

    tag: str
    scope: str
    element_id: str | None = None
    parent_id: str | None = None

    def describe(self) -> str:
        located = f" '{self.element_id}'" if self.element_id else ""
        within = f" in {self.scope} '{self.parent_id}'" if self.parent_id else ""
        return f"<{self.tag}>{located}{within}"


@runtime_checkable
class DiagramConverter(Protocol):
    """Stateless converter between external bytes and canonical BPMN IR."""

    def parse(self, payload: bytes) -> BpmnDiagram:
        """Parse bytes and return the canonical diagram model."""
        ...

    def parse_with_diagnostics(
        self, payload: bytes
    ) -> tuple[BpmnDiagram, list[UnsupportedElement]]:
        """Parse bytes, also reporting what the IR does not represent."""
        ...

    def serialize(self, diagram: BpmnDiagram) -> bytes:
        """Serialise the canonical diagram model to external bytes."""
        ...


class BaseDiagramConverter:
    """Default diagnostics behaviour for formats that lose nothing on parse."""

    def parse(self, payload: bytes) -> BpmnDiagram:  # pragma: no cover - abstract
        raise NotImplementedError

    def parse_with_diagnostics(
        self, payload: bytes
    ) -> tuple[BpmnDiagram, list[UnsupportedElement]]:
        """Parse and report nothing unsupported — the format is the IR itself."""
        return self.parse(payload), []
