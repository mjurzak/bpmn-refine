"""OpenAI provider (GPT models).

Also used by Ollama — pass base_url to point at a local server.
"""
from __future__ import annotations

import openai


class OpenAIProvider:
    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(
        self,
        prompt: str,
        system: str | None,
        model: str,
        max_tokens: int,
    ) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = await self._client.chat.completions.create(
            model=model, max_tokens=max_tokens, messages=messages
        )
        return response.choices[0].message.content or ""

    async def complete_with_history(
        self,
        messages: list[dict],
        system: str | None,
        model: str,
        max_tokens: int,
    ) -> str:
        # OpenAI takes system as the first message in the list
        full_messages: list[dict] = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)
        response = await self._client.chat.completions.create(
            model=model, max_tokens=max_tokens, messages=full_messages
        )
        return response.choices[0].message.content or ""
