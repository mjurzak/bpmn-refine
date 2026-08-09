"""Canonical Pydantic BPMN IR serialised as compact-key JSON."""

from __future__ import annotations

import json
from typing import Any

from app.model.protocol import BaseDiagramConverter
from app.model.schema import BpmnDiagram

_DIAGRAM_KEYS = {
    "definitions_id": "d",
    "target_namespace": "t",
    "processes": "p",
    "namespaces": "n",
}
_PROCESS_KEYS = {
    "id": "i",
    "name": "n",
    "is_executable": "x",
    "flow_nodes": "v",
    "sequence_flows": "e",
    "pools": "p",
}
_NODE_KEYS = {
    "id": "i",
    "type": "t",
    "name": "n",
    "outgoing": "o",
    "incoming": "m",
    "event_definitions": "d",
    "extra": "x",
}
_EVENT_DEFINITION_KEYS = {"type": "t", "id": "i", "extra": "x"}
_FLOW_KEYS = {
    "id": "i",
    "source_ref": "s",
    "target_ref": "t",
    "name": "n",
    "condition_expression": "c",
}
_POOL_KEYS = {"id": "i", "name": "n", "lanes": "l"}
_LANE_KEYS = {"id": "i", "name": "n", "flow_node_refs": "r"}


class CompactJsonConverter(BaseDiagramConverter):
    """Converts between compact-key JSON bytes and canonical BpmnDiagram."""

    def parse(self, payload: bytes) -> BpmnDiagram:
        """Parse compact-key JSON bytes into a BpmnDiagram."""
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, dict):
            raise TypeError("Compact JSON diagram payload must be an object")
        return BpmnDiagram.model_validate(_expand_diagram(data))

    def serialize(self, diagram: BpmnDiagram) -> bytes:
        """Serialise a BpmnDiagram into stable compact-key JSON bytes."""
        data = _compact_diagram(diagram.model_dump(mode="json"))
        return json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")


def _compact_diagram(data: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            _DIAGRAM_KEYS["definitions_id"]: data["definitions_id"],
            _DIAGRAM_KEYS["target_namespace"]: data.get("target_namespace"),
            _DIAGRAM_KEYS["processes"]: [
                _compact_process(item) for item in data.get("processes", [])
            ],
            _DIAGRAM_KEYS["namespaces"]: data.get("namespaces", {}),
        }
    )


def _compact_process(data: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            _PROCESS_KEYS["id"]: data["id"],
            _PROCESS_KEYS["name"]: data.get("name"),
            _PROCESS_KEYS["is_executable"]: data.get("is_executable"),
            _PROCESS_KEYS["flow_nodes"]: [
                _compact_node(item) for item in data.get("flow_nodes", [])
            ],
            _PROCESS_KEYS["sequence_flows"]: [
                _compact_flow(item) for item in data.get("sequence_flows", [])
            ],
            _PROCESS_KEYS["pools"]: [
                _compact_pool(item) for item in data.get("pools", [])
            ],
        }
    )


def _compact_node(data: dict[str, Any]) -> dict[str, Any]:
    compacted = {_NODE_KEYS[key]: data.get(key) for key in _NODE_KEYS}
    compacted[_NODE_KEYS["event_definitions"]] = [
        _drop_empty(
            {
                _EVENT_DEFINITION_KEYS[key]: item.get(key)
                for key in _EVENT_DEFINITION_KEYS
            }
        )
        for item in data.get("event_definitions", [])
    ]
    return _drop_empty(compacted)


def _compact_flow(data: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty({_FLOW_KEYS[key]: data.get(key) for key in _FLOW_KEYS})


def _compact_pool(data: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            _POOL_KEYS["id"]: data["id"],
            _POOL_KEYS["name"]: data.get("name"),
            _POOL_KEYS["lanes"]: [
                _compact_lane(item) for item in data.get("lanes", [])
            ],
        }
    )


def _compact_lane(data: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty({_LANE_KEYS[key]: data.get(key) for key in _LANE_KEYS})


def _expand_diagram(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "definitions_id": data["d"],
        "target_namespace": data.get("t", "http://bpmn.io/schema/bpmn"),
        "processes": [_expand_process(item) for item in data.get("p", [])],
        "namespaces": data.get("n", {}),
    }


def _expand_process(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": data["i"],
        "name": data.get("n"),
        "is_executable": data.get("x", False),
        "flow_nodes": [_expand_node(item) for item in data.get("v", [])],
        "sequence_flows": [_expand_flow(item) for item in data.get("e", [])],
        "pools": [_expand_pool(item) for item in data.get("p", [])],
    }


def _expand_node(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": data["i"],
        "type": data["t"],
        "name": data.get("n"),
        "outgoing": data.get("o", []),
        "incoming": data.get("m", []),
        "event_definitions": [
            {"type": item["t"], "id": item.get("i"), "extra": item.get("x", {})}
            for item in data.get("d", [])
        ],
        "extra": data.get("x", {}),
    }


def _expand_flow(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": data["i"],
        "source_ref": data["s"],
        "target_ref": data["t"],
        "name": data.get("n"),
        "condition_expression": data.get("c"),
    }


def _expand_pool(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": data["i"],
        "name": data.get("n"),
        "lanes": [_expand_lane(item) for item in data.get("l", [])],
    }


def _expand_lane(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": data["i"],
        "name": data.get("n"),
        "flow_node_refs": data.get("r", []),
    }


def _drop_empty(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in data.items()
        if value is not None and value != [] and value != {} and value is not False
    }
