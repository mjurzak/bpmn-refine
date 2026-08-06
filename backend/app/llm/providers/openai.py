"""OpenAI provider (GPT models), also the base for other OpenAI-compatible servers.

The neutral `max_tokens` argument becomes `max_completion_tokens`; subclasses whose
server uses a different field name override `_output_limit`.
"""
from __future__ import annotations

from typing import Any

import openai

from app.llm.protocol import LlmResponse
from app.llm.usage import from_openai


class OpenAIProvider:
    # subclasses (Ollama) flip this off if the local server rejects strict mode
    supports_strict: bool = True
    # which reporting convention the usage block follows; subclasses override it
    usage_source: str = "openai"
    supports_reasoning_effort: bool = True
    supports_temperature: bool = True
    # the API treats seed as best-effort: it narrows variation, it does not remove it
    supports_seed: bool = True

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    def _sampling(self, temperature: float | None, seed: int | None) -> dict[str, Any]:
        controls: dict[str, Any] = {}
        if temperature is not None and self.supports_temperature:
            controls["temperature"] = temperature
        if seed is not None and self.supports_seed:
            controls["seed"] = seed
        return controls

    def _output_limit(self, max_tokens: int) -> dict[str, int]:
        return {"max_completion_tokens": max_tokens}

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
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            **self._output_limit(max_tokens),
        }
        if reasoning_effort and self.supports_reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        kwargs.update(self._sampling(temperature, seed))
        response = await self._client.chat.completions.create(**kwargs)
        return self._response(response)

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
        full_messages: list[dict] = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": full_messages,
            **self._output_limit(max_tokens),
        }
        if reasoning_effort and self.supports_reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        kwargs.update(self._sampling(temperature, seed))
        response = await self._client.chat.completions.create(**kwargs)
        return self._response(response)

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
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            **self._output_limit(max_tokens),
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": self.supports_strict,
                    "schema": schema,
                },
            },
        }
        if reasoning_effort and self.supports_reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        kwargs.update(self._sampling(temperature, seed))
        response = await self._client.chat.completions.create(**kwargs)
        return self._response(response)

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
        full_messages: list[dict] = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": full_messages,
            **self._output_limit(max_tokens),
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": self.supports_strict,
                    "schema": schema,
                },
            },
        }
        if reasoning_effort and self.supports_reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        kwargs.update(self._sampling(temperature, seed))
        response = await self._client.chat.completions.create(**kwargs)
        return self._response(response)

    def _response(self, response: Any) -> LlmResponse:
        text = response.choices[0].message.content or ""
        return LlmResponse(text, from_openai(response, source=self.usage_source))
