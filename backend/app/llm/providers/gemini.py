"""Gemini provider (Google models via the google-genai SDK).

Structured outputs use controlled generation; Gemini does not resolve `$ref`, so schemas are inlined.
"""
from __future__ import annotations

from typing import Any, cast

from google import genai
from google.genai import types

from app.llm.protocol import LlmResponse
from app.llm.schema import inline_defs
from app.llm.usage import from_gemini


class GeminiProvider:
    supports_temperature = True
    supports_seed = True

    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    def _sampling(self, temperature: float | None, seed: int | None) -> dict[str, Any]:
        controls: dict[str, Any] = {}
        if temperature is not None:
            controls["temperature"] = temperature
        if seed is not None:
            controls["seed"] = seed
        return controls

    def _reasoning(self, reasoning_effort: str | None) -> dict[str, Any]:
        if reasoning_effort is None:
            return {}
        if reasoning_effort not in {"low", "medium", "high"}:
            raise ValueError(
                "Gemini reasoning_effort must be one of: low, medium, high"
            )
        return {
            "thinking_config": types.ThinkingConfig(
                thinking_level=reasoning_effort,
            )
        }

    async def complete(
        self,
        prompt: str,
        system: str | None,
        model: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> LlmResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            **self._reasoning(reasoning_effort),
            **self._sampling(temperature, seed),
        )
        response = await self._client.aio.models.generate_content(
            model=model, contents=prompt, config=config
        )
        return LlmResponse(response.text or "", from_gemini(response))

    async def complete_with_history(
        self,
        messages: list[dict],
        system: str | None,
        model: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> LlmResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            **self._reasoning(reasoning_effort),
            **self._sampling(temperature, seed),
        )
        contents = cast(types.ContentListUnionDict, _to_contents(messages))
        response = await self._client.aio.models.generate_content(
            model=model, contents=contents, config=config
        )
        return LlmResponse(response.text or "", from_gemini(response))

    async def complete_structured(
        self,
        prompt: str,
        system: str | None,
        model: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> LlmResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
            response_schema=inline_defs(schema),
            **self._reasoning(reasoning_effort),
            **self._sampling(temperature, seed),
        )
        response = await self._client.aio.models.generate_content(
            model=model, contents=prompt, config=config
        )
        return LlmResponse(response.text or "", from_gemini(response))

    async def complete_structured_with_history(
        self,
        messages: list[dict],
        system: str | None,
        model: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> LlmResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
            response_schema=inline_defs(schema),
            **self._reasoning(reasoning_effort),
            **self._sampling(temperature, seed),
        )
        contents = cast(types.ContentListUnionDict, _to_contents(messages))
        response = await self._client.aio.models.generate_content(
            model=model, contents=contents, config=config
        )
        return LlmResponse(response.text or "", from_gemini(response))


def _to_contents(messages: list[dict]) -> list[dict]:
    """Map OpenAI-style roles onto Gemini's `contents` shape (assistant -> model)."""
    contents: list[dict] = []
    for message in messages:
        role = "model" if message["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": message["content"]}]})
    return contents
