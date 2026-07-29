"""JSON-schema helpers for provider structured-output APIs.

Providers expose schema-constrained output through slightly different shapes
(Anthropic `output_config.format`, OpenAI/Ollama `response_format` json_schema,
Gemini `responseSchema`).  They share two requirements that Pydantic's raw
`model_json_schema()` does not satisfy out of the box:

- strict providers (Anthropic, OpenAI) want every object to set
  `additionalProperties: false` and list all its keys in `required`
- Gemini does not resolve `$ref`/`$defs`, so the schema must be inlined

The helpers here turn a Pydantic model or `TypeAdapter` into a schema each
provider accepts.  Schemas must be non-recursive (the BPMN IR is — subprocess
nesting is intentionally out of scope), otherwise `inline_defs` would not
terminate.
"""
from __future__ import annotations

import copy
from typing import Any, Protocol


class _SchemaSource(Protocol):
    def json_schema(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...


def strict_json_schema(source: Any) -> dict[str, Any]:
    """Return a strict JSON schema for a Pydantic model or TypeAdapter.

    Accepts a BaseModel subclass (uses `model_json_schema()`) or a TypeAdapter
    (uses `json_schema()`).  Every object gets `additionalProperties: false` and
    a `required` entry covering all its declared properties — the shape strict
    structured-output APIs validate against.
    """
    if hasattr(source, "model_json_schema"):
        schema = source.model_json_schema()
    else:
        schema = source.json_schema()
    return _make_strict(copy.deepcopy(schema))


def inline_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline every `$ref` into `$defs`, returning a self-contained schema.

    Gemini's controlled generation does not resolve references, so the schema
    handed to it must already be flattened.  Only safe for non-recursive schemas.
    """
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
