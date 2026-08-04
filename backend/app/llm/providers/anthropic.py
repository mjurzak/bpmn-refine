"""Anthropic provider (Claude models)."""
from __future__ import annotations

from typing import Any

import anthropic

from app.llm.protocol import LlmResponse
from app.llm.usage import from_anthropic


class AnthropicProvider:
    supports_temperature = True
    # the Messages API exposes no sampling seed, so a configured seed is recorded
    # as unsupported rather than passed and quietly dropped
    supports_seed = False

    def __init__(self, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    def _sampling(self, temperature: float | None) -> dict:
        return {} if temperature is None else {"temperature": temperature}

    def _output_config(
        self,
        reasoning_effort: str | None,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config: dict[str, Any] = {}
        if reasoning_effort and reasoning_effort != "none":
            config["effort"] = reasoning_effort
        if schema is not None:
            config["format"] = {"type": "json_schema", "schema": schema}
        return config

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
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        output_config = self._output_config(reasoning_effort)
        if output_config:
            kwargs["output_config"] = output_config
        kwargs.update(self._sampling(temperature))
        response = await self._client.messages.create(**kwargs)
        return LlmResponse(response.content[0].text, from_anthropic(response))

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
        kwargs: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        output_config = self._output_config(reasoning_effort)
        if output_config:
            kwargs["output_config"] = output_config
        kwargs.update(self._sampling(temperature))
        response = await self._client.messages.create(**kwargs)
        return LlmResponse(response.content[0].text, from_anthropic(response))

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
        # output_config.format constrains the final response to the JSON schema;
        # the first text block is then guaranteed-valid JSON
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": self._output_config(reasoning_effort, schema),
        }
        if system:
            kwargs["system"] = system
        kwargs.update(self._sampling(temperature))
        response = await self._client.messages.create(**kwargs)
        text = next(block.text for block in response.content if block.type == "text")
        return LlmResponse(text, from_anthropic(response))

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
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "output_config": self._output_config(reasoning_effort, schema),
        }
        if system:
            kwargs["system"] = system
        kwargs.update(self._sampling(temperature))
        response = await self._client.messages.create(**kwargs)
        text = next(block.text for block in response.content if block.type == "text")
        return LlmResponse(text, from_anthropic(response))
