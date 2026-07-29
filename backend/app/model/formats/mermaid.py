"""Mermaid flowchart representation with compact IR metadata."""

from __future__ import annotations

from app.model.formats.compact_json import CompactJsonConverter
from app.model.protocol import BaseDiagramConverter
from app.model.schema import BpmnDiagram, FlowNode, FlowNodeType

_METADATA_PREFIX = "%% bpmn-ai-ir:"


class MermaidConverter(BaseDiagramConverter):
    """Converts between Mermaid flowchart bytes and canonical BpmnDiagram."""

    def parse(self, payload: bytes) -> BpmnDiagram:
        """Parse Mermaid bytes with embedded compact IR metadata."""
        for line in payload.decode("utf-8").splitlines():
            if line.startswith(_METADATA_PREFIX):
                compact_payload = line.removeprefix(_METADATA_PREFIX).encode("utf-8")
                return CompactJsonConverter().parse(compact_payload)
        raise ValueError("Mermaid diagram payload is missing BPMN IR metadata")

    def serialize(self, diagram: BpmnDiagram) -> bytes:
        """Serialise a BpmnDiagram into Mermaid flowchart bytes."""
        metadata = CompactJsonConverter().serialize(diagram).decode("utf-8")
        lines = [
            "%% bpmn-ai-mermaid:v1",
            f"{_METADATA_PREFIX}{metadata}",
            "flowchart TD",
        ]

        for proc_index, proc in enumerate(diagram.processes):
            node_aliases = {
                node.id: f"p{proc_index}n{index}"
                for index, node in enumerate(proc.flow_nodes)
            }
            if proc.name or len(diagram.processes) > 1:
                lines.append(f"  %% process: {_clean_label(proc.name or proc.id)}")
            for node in proc.flow_nodes:
                lines.append(f"  {node_aliases[node.id]}{_node_shape(node)}")
            for flow in proc.sequence_flows:
                source = node_aliases.get(flow.source_ref)
                target = node_aliases.get(flow.target_ref)
                if source is None or target is None:
                    continue
                label = _flow_label(flow.name, flow.condition_expression)
                lines.append(f"  {source}{label}{target}")

        return ("\n".join(lines) + "\n").encode("utf-8")


def _node_shape(node: FlowNode) -> str:
    label = _clean_label(node.name or node.id)
    if node.type in {
        FlowNodeType.START_EVENT,
        FlowNodeType.END_EVENT,
        FlowNodeType.INTERMEDIATE_CATCH_EVENT,
        FlowNodeType.INTERMEDIATE_THROW_EVENT,
    }:
        return f"(({label}))"
    if "Gateway" in node.type.value or "gateway" in node.type.value:
        return f"{{{label}}}"
    return f"[{label}]"


def _flow_label(name: str | None, condition: str | None) -> str:
    label = _clean_label(name or condition or "")
    if label:
        return f" -- {label} --> "
    return " --> "


def _clean_label(value: str) -> str:
    return (
        value.replace("\n", " ")
        .replace("\r", " ")
        .replace("[", "(")
        .replace("]", ")")
        .replace("{", "(")
        .replace("}", ")")
        .replace('"', "'")
        .strip()
    )
