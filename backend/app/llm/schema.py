"""JSON-schema helpers for provider structured-output APIs.

Two things Pydantic's raw `model_json_schema()` does not give:

- strict providers (Anthropic, OpenAI) want `additionalProperties: false` and all keys in `required`
- Gemini does not resolve `$ref`/`$defs`, so the schema must be inlined

Schemas must be non-recursive, otherwise `inline_defs` does not terminate.
"""
from __future__ import annotations

import copy
from typing import Any, Protocol


class _SchemaSource(Protocol):
    def json_schema(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...


def strict_json_schema(source: Any) -> dict[str, Any]:
    """Return a strict JSON schema for a Pydantic model or TypeAdapter."""
    if hasattr(source, "model_json_schema"):
        schema = source.model_json_schema()
    else:
        schema = source.json_schema()
    return _make_strict(copy.deepcopy(schema))


def inline_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline every `$ref` into `$defs` for Gemini, which does not resolve them. Non-recursive schemas only."""
    defs = schema.get("$defs", {})
    resolved = _resolve_refs(copy.deepcopy(schema), defs)
    resolved.pop("$defs", None)
    return resolved


def _make_strict(node: Any) -> Any:
    if isinstance(node, dict):
        # recurse first so nested $defs and properties are covered too
        for key, value in list(node.items()):
            node[key] = _make_strict(value)
        if "$ref" in node:
            return {"$ref": node["$ref"]}
        # pydantic emits discriminated unions as `oneOf`; strict APIs speak `anyOf`
        if "oneOf" in node and "anyOf" not in node:
            node["anyOf"] = node.pop("oneOf")
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        return node
    if isinstance(node, list):
        return [_make_strict(item) for item in node]
    return node


def _resolve_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = defs[ref.split("/")[-1]]
            return _resolve_refs(copy.deepcopy(target), defs)
        return {key: _resolve_refs(value, defs) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(item, defs) for item in node]
    return node
