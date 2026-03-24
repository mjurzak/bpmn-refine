"""LLM provider registry.

Usage:
    from app.llm.registry import get_provider

    provider = get_provider()           # uses LLM_PROVIDER from settings
    provider = get_provider("openai")   # explicit
"""
from __future__ import annotations

import logging

from app.llm.protocol import LLMProvider

logger = logging.getLogger(__name__)

_registry: dict[str, LLMProvider] = {}


def register(name: str, provider: LLMProvider) -> None:
    """Register a provider under a short name (e.g. "anthropic")."""
    _registry[name] = provider


def get_provider(name: str | None = None) -> LLMProvider:
    """Return a provider by name, falling back to the setting default."""
    if name is None:
        from app.core.config import settings
        name = settings.llm_provider
    if name not in _registry:
        raise KeyError(
            f"No LLM provider registered under '{name}'. "
            f"Available: {list(_registry)}"
        )
    return _registry[name]


def _bootstrap() -> None:
    """Register all providers whose credentials are present in settings."""
    from app.core.config import settings

    from app.llm.providers.anthropic import AnthropicProvider
    from app.llm.providers.openai import OpenAIProvider
    from app.llm.providers.ollama import OllamaProvider

    print(f"[llm bootstrap] provider={settings.llm_provider!r}")
    print(f"[llm bootstrap] anthropic_api_key present: {bool(settings.anthropic_api_key)}, len={len(settings.anthropic_api_key)}")
    print(f"[llm bootstrap] openai_api_key present: {bool(settings.openai_api_key)}, len={len(settings.openai_api_key)}")

    if settings.anthropic_api_key:
        register("anthropic", AnthropicProvider(settings.anthropic_api_key))
        print("[llm bootstrap] registered: anthropic")

    if settings.openai_api_key:
        register("openai", OpenAIProvider(settings.openai_api_key))
        print("[llm bootstrap] registered: openai")

    # Ollama runs locally — no key needed
    register("ollama", OllamaProvider(settings.ollama_base_url))
    print("[llm bootstrap] registered: ollama")


_bootstrap()
