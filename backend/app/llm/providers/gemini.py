"""Gemini provider (Google models via the google-genai SDK).

Structured outputs use controlled generation: `response_mime_type` plus a
`response_schema`.  Gemini does not resolve `$ref`, so the schema is inlined
before being handed over.
"""
from __future__ import annotations

from typing import Any, cast

from google import genai
from google.genai import types

from app.llm.schema import inline_defs


class GeminiProvider:
    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    async def complete(
        self,
        prompt: str,
        system: str | None,
        model: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
        )
        response = await self._client.aio.models.generate_content(
            model=model, contents=prompt, config=config
        )
        return response.text or ""

    async def complete_with_history(
        self,
        messages: list[dict],
        system: str | None,
        model: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
        )
        contents = cast(types.ContentListUnionDict, _to_contents(messages))
        response = await self._client.aio.models.generate_content(
            model=model, contents=contents, config=config
        )
        return response.text or ""

    async def complete_structured(
        self,
        prompt: str,
        system: str | None,
        model: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
            response_schema=inline_defs(schema),
        )
        response = await self._client.aio.models.generate_content(
            model=model, contents=prompt, config=config
        )
        return response.text or ""


def _to_contents(messages: list[dict]) -> list[dict]:
    """Map OpenAI-style roles onto Gemini's `contents` shape (assistant -> model)."""
    contents: list[dict] = []
    for message in messages:
        role = "model" if message["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": message["content"]}]})
    return contents
