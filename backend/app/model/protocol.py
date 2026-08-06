"""Converter protocol — the contract every representation format must satisfy.

A new representation implements this protocol and registers via registry.register().

`parse` returns the model alone, keeping parse(serialize(d)) == d readable.
`parse_with_diagnostics` also reports what the IR does not represent; diagnostics
stay off `BpmnDiagram` so a re-parse of exported bytes still compares equal to
its source model.
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
