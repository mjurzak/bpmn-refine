"""Ollama provider: OpenAIProvider pointed at the local /v1 endpoint.

Structured outputs go through Ollama's constrained decoding (GBNF), which honours the
response schema but not the OpenAI `strict` flag.
"""
from __future__ import annotations

from app.llm.providers.openai import OpenAIProvider


class OllamaProvider(OpenAIProvider):
    supports_strict = False
    supports_reasoning_effort = False
    # the tokenizer is the local model's, so these totals are not comparable to hosted ones
    usage_source = "ollama"

    def _output_limit(self, max_tokens: int) -> dict[str, int]:
        return {"max_tokens": max_tokens}

    def __init__(self, base_url: str) -> None:
        # ollama ignores the key, but the openai client requires a non-empty string
        super().__init__(api_key="ollama", base_url=f"{base_url.rstrip('/')}/v1")
