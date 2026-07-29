"""Ollama provider (local models via OpenAI-compatible API).

Ollama exposes an OpenAI-compatible endpoint at /v1, so this is a thin
wrapper around OpenAIProvider with a custom base URL.  Structured outputs map
to Ollama's `format`=schema constrained decoding (GBNF), a real structural
guarantee — so no fenced-JSON fallback is needed.  Ollama honours the response
schema but not the OpenAI `strict` flag, so that is left off here.
"""
from __future__ import annotations

from app.llm.providers.openai import OpenAIProvider


class OllamaProvider(OpenAIProvider):
    supports_strict = False
    supports_reasoning_effort = False
    # the wire format is OpenAI's, but the tokenizer behind it is the local
    # model's — a token total from here is not comparable to a hosted one
    usage_source = "ollama"

    def _output_limit(self, max_tokens: int) -> dict[str, int]:
        return {"max_tokens": max_tokens}

    def __init__(self, base_url: str) -> None:
        # Ollama doesn't validate the key but the openai client requires a non-empty string
        super().__init__(api_key="ollama", base_url=f"{base_url.rstrip('/')}/v1")
