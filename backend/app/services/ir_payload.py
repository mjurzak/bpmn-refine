"""LLM-facing diagram payload serialisation helpers."""

from __future__ import annotations

import json
from typing import Any

from app.experiments import ExperimentConfig, IrFormat
from app.model.registry import get_converter
from app.model.schema import BpmnDiagram

_JSON_FENCES = {IrFormat.PYDANTIC, IrFormat.PYDANTIC_JSON, IrFormat.COMPACT_JSON}


def diagram_payload(diagram: BpmnDiagram, config: ExperimentConfig | None) -> Any:
    """Return the diagram payload matching the selected experiment IR format."""
    active_config = config or ExperimentConfig()
    if active_config.ir_format == IrFormat.PYDANTIC:
        return diagram.model_dump(mode="json")
    return diagram_payload_text(diagram, active_config)


def diagram_payload_text(diagram: BpmnDiagram, config: ExperimentConfig | None) -> str:
    """Return a text form suitable for fenced prompt context blocks."""
    active_config = config or ExperimentConfig()
    if active_config.ir_format == IrFormat.PYDANTIC:
        return diagram.model_dump_json(indent=2)
    return _candidate_converter(active_config).serialize(diagram).decode("utf-8")


def parse_diagram_payload(payload: Any, config: ExperimentConfig | None) -> BpmnDiagram:
    """Parse an LLM-emitted diagram payload using the selected IR format."""
    active_config = config or ExperimentConfig()
    if active_config.ir_format == IrFormat.PYDANTIC:
        if isinstance(payload, str):
            return BpmnDiagram.model_validate_json(payload)
        return BpmnDiagram.model_validate(payload)
    if isinstance(payload, bytes):
        encoded = payload
    elif isinstance(payload, str):
        encoded = payload.encode("utf-8")
    else:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return _candidate_converter(active_config).parse(encoded)


def parse_diagram_from_fenced_reply(
    reply: str,
    config: ExperimentConfig | None,
) -> BpmnDiagram | None:
    """Extract and parse the first compatible fenced diagram block from a reply."""
    last_error: Exception | None = None
    for fence in _candidate_fences(config):
        opening = f"```{fence}"
        if opening not in reply:
            continue
        start = reply.index(opening) + len(opening)
        end = reply.index("```", start)
        try:
            return parse_diagram_payload(reply[start:end].strip(), config)
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return None


def diagram_fence(config: ExperimentConfig | None) -> str:
    active_config = config or ExperimentConfig()
    if active_config.ir_format in _JSON_FENCES:
        return "json"
    return str(active_config.ir_format)


def _candidate_converter(config: ExperimentConfig):
    return get_converter(str(config.ir_format))


def _candidate_fences(config: ExperimentConfig | None) -> list[str]:
    active_config = config or ExperimentConfig()
    preferred = [diagram_fence(active_config), str(active_config.ir_format)]
    fallbacks = ["diagram", "ir"]
    fences: list[str] = []
    for fence in preferred + fallbacks:
        if fence not in fences:
            fences.append(fence)
    return fences
