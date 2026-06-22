"""Anthropic provider (Claude models)."""
from __future__ import annotations

from typing import Any

import anthropic


class AnthropicProvider:
    def __init__(self, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        prompt: str,
        system: str | None,
        model: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> str:
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        if reasoning_effort and reasoning_effort != "none":
            kwargs["effort"] = reasoning_effort
        response = await self._client.messages.create(**kwargs)
        return response.content[0].text

    async def complete_with_history(
        self,
        messages: list[dict],
        system: str | None,
        model: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> str:
        kwargs: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        if reasoning_effort and reasoning_effort != "none":
            kwargs["effort"] = reasoning_effort
        response = await self._client.messages.create(**kwargs)
        return response.content[0].text

    async def complete_structured(
        self,
        prompt: str,
        system: str | None,
        model: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> str:
        # output_config.format constrains the final response to the JSON schema;
        # the first text block is then guaranteed-valid JSON
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if system:
            kwargs["system"] = system
        if reasoning_effort and reasoning_effort != "none":
            kwargs["effort"] = reasoning_effort
        response = await self._client.messages.create(**kwargs)
        return next(block.text for block in response.content if block.type == "text")
