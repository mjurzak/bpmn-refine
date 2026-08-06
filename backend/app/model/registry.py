"""Converter registry.

Usage:
    from app.model.registry import get_converter

    converter = get_converter()            # uses DIAGRAM_CONVERTER from settings
    converter = get_converter("pydantic")  # explicit
"""
from __future__ import annotations

from app.model.protocol import DiagramConverter

_registry: dict[str, DiagramConverter] = {}


def register(name: str, converter: DiagramConverter) -> None:
    """Register a converter under a short name (e.g. "pydantic")."""
    _registry[name] = converter


def get_converter(name: str | None = None) -> DiagramConverter:
    """Return a converter by name, falling back to the setting default."""
    if name is None:
        from app.core.config import settings
        name = settings.diagram_converter
    if name not in _registry:
        raise KeyError(
            f"No converter registered under '{name}'. "
            f"Available: {list(_registry)}"
        )
    return _registry[name]


# importing this module is enough to make get_converter() work
def _bootstrap() -> None:
    from app.model.formats.compact_json import CompactJsonConverter
    from app.model.formats.mermaid import MermaidConverter
    from app.model.formats.pydantic_ir import PydanticConverter
    from app.model.formats.pydantic_json import PydanticJsonConverter
    from app.model.formats.yaml_ir import YamlConverter

    register("pydantic", PydanticConverter())
    register("pydantic_json", PydanticJsonConverter())
    register("pydantic-json", PydanticJsonConverter())
    register("yaml", YamlConverter())
    register("mermaid", MermaidConverter())
    register("compact_json", CompactJsonConverter())
    register("compact-json", CompactJsonConverter())


_bootstrap()
