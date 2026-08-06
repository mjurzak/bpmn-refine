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
            f"No LLM provider registered under '{name}'. Available: {list(_registry)}"
        )
    return _registry[name]


def _bootstrap() -> None:
    """Register all providers whose credentials are present in settings."""
    from app.core.config import settings
    from app.llm.providers.anthropic import AnthropicProvider
    from app.llm.providers.ollama import OllamaProvider
    from app.llm.providers.openai import OpenAIProvider

    logger.debug("llm bootstrap provider=%r", settings.llm_provider)
    logger.debug(
        "anthropic key present=%s len=%d",
        bool(settings.anthropic_api_key),
        len(settings.anthropic_api_key),
    )
    logger.debug(
        "openai key present=%s len=%d",
        bool(settings.openai_api_key),
        len(settings.openai_api_key),
    )

    if settings.anthropic_api_key:
        register("anthropic", AnthropicProvider(settings.anthropic_api_key))
        logger.debug("registered anthropic provider")

    if settings.openai_api_key:
        register("openai", OpenAIProvider(settings.openai_api_key))
        logger.debug("registered openai provider")

    if settings.gemini_api_key:
        # lazy import so google-genai is only required when gemini is configured
        from app.llm.providers.gemini import GeminiProvider

        register("gemini", GeminiProvider(settings.gemini_api_key))
        logger.debug("registered gemini provider")

    # ollama runs locally, no key needed
    register("ollama", OllamaProvider(settings.ollama_base_url))
    logger.debug("registered ollama provider")


_bootstrap()
