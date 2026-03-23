"""Ollama provider (local models via OpenAI-compatible API).

Ollama exposes an OpenAI-compatible endpoint at /v1, so this is a thin
wrapper around OpenAIProvider with a custom base URL.
"""
from __future__ import annotations

from app.llm.providers.openai import OpenAIProvider


class OllamaProvider(OpenAIProvider):
    def __init__(self, base_url: str) -> None:
        # Ollama doesn't validate the key but the openai client requires a non-empty string
        super().__init__(api_key="ollama", base_url=f"{base_url.rstrip('/')}/v1")
