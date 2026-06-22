"""OpenAI provider (GPT models).

Also used by Ollama — pass base_url to point at a local server.  Ollama's
OpenAI-compatible endpoint accepts the same `response_format` json_schema, which
it serves via constrained decoding, so structured outputs work unchanged there.

`max_tokens` is accepted for protocol parity but not forwarded: newer OpenAI
models reject `max_tokens` (they want `max_completion_tokens`), while the
OpenAI-compatible servers we target accept neither name uniformly — letting the
server apply its own default keeps both paths working.
"""
from __future__ import annotations

from typing import Any

import openai


class OpenAIProvider:
    # subclasses (Ollama) flip this off if the local server rejects strict mode
    supports_strict: bool = True
    supports_reasoning_effort: bool = True

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(
        self,
        prompt: str,
        system: str | None,
        model: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if reasoning_effort and self.supports_reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        response = await self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    async def complete_with_history(
        self,
        messages: list[dict],
        system: str | None,
        model: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> str:
        # OpenAI takes system as the first message in the list
        full_messages: list[dict] = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)
        kwargs: dict[str, Any] = {"model": model, "messages": full_messages}
        if reasoning_effort and self.supports_reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        response = await self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    async def complete_structured(
        self,
        prompt: str,
        system: str | None,
        model: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
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
        response = await self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""
