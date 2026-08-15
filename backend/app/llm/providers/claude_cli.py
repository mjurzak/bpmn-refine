"""Claude Code CLI provider using normal saved subscription authentication."""
from __future__ import annotations

import json
import os
from typing import Any

from app.llm.protocol import LlmResponse
from app.llm.providers.cli import (
    CliOutputError,
    IsolatedCliCall,
    history_prompt,
    run_cli,
)
from app.llm.usage import from_cli


class ClaudeCliProvider:
    supports_temperature = False
    supports_seed = False
    supports_reasoning_effort = True
    supports_max_tokens = False

    def __init__(
        self,
        executable: str = "claude",
        timeout_seconds: float = 300.0,
        harness_version: str | None = None,
    ) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.harness_version = harness_version

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
        return await self._complete(prompt, system, model, reasoning_effort)

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
        return await self._complete(
            history_prompt(messages), system, model, reasoning_effort
        )

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
        return await self._complete(prompt, system, model, reasoning_effort, schema)

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
        return await self._complete(
            history_prompt(messages), system, model, reasoning_effort, schema
        )

    async def _complete(
        self,
        prompt: str,
        system: str | None,
        model: str,
        reasoning_effort: str | None,
        schema: dict[str, Any] | None = None,
    ) -> LlmResponse:
        with IsolatedCliCall() as cwd:
            args = [
                self.executable,
                "-p",
                "--output-format",
                "json",
                "--no-session-persistence",
                "--safe-mode",
                "--prompt-suggestions",
                "false",
                "--tools",
                "",
                "--max-turns",
                "1",
                "--model",
                model,
            ]
            if system:
                args.extend(["--system-prompt", system])
            if reasoning_effort and reasoning_effort != "none":
                args.extend(["--effort", reasoning_effort])
            if schema is not None:
                args.extend(["--json-schema", json.dumps(schema, ensure_ascii=False)])
            stdout, _ = await run_cli(
                args,
                prompt,
                self.timeout_seconds,
                cwd=cwd,
                env=_controlled_environment(reasoning_effort),
            )
        return parse_claude_json(stdout, structured=schema is not None)


def _controlled_environment(reasoning_effort: str | None) -> dict[str, str]:
    """Keep auth and transport settings while removing ambient reasoning overrides."""
    env = os.environ.copy()
    for name in (
        "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING",
        "CLAUDE_CODE_DISABLE_THINKING",
        "CLAUDE_CODE_EFFORT_LEVEL",
        "MAX_THINKING_TOKENS",
    ):
        env.pop(name, None)
    if reasoning_effort == "none":
        env["CLAUDE_CODE_DISABLE_THINKING"] = "1"
    return env


def parse_claude_json(stdout: str, *, structured: bool = False) -> LlmResponse:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CliOutputError(f"Claude Code CLI emitted malformed JSON: {exc.msg}") from exc
    if not isinstance(envelope, dict):
        raise CliOutputError("Claude Code CLI JSON envelope is not an object")
    if envelope.get("is_error") is True or envelope.get("subtype") not in {
        None,
        "success",
    }:
        detail = envelope.get("result") or envelope.get("error") or envelope.get("subtype")
        raise CliOutputError(f"Claude Code CLI reported an unsuccessful result: {detail}")
    usage = _claude_usage(envelope)
    if structured:
        if "structured_output" not in envelope:
            raise CliOutputError(
                "Claude Code CLI JSON envelope has no structured_output field"
            )
        text = json.dumps(envelope["structured_output"], ensure_ascii=False)
    else:
        text = envelope.get("result")
        if not isinstance(text, str):
            raise CliOutputError("Claude Code CLI JSON envelope has no result text")
    if not text.strip():
        raise CliOutputError("Claude Code CLI returned empty output")
    return LlmResponse(text, usage)


def _claude_usage(envelope: dict[str, Any]):
    usage = envelope.get("usage")
    if isinstance(usage, dict):
        parsed = from_cli(usage, source="claude_cli")
        if parsed is not None:
            return parsed
    model_usage = envelope.get("modelUsage")
    if isinstance(model_usage, dict):
        totals: dict[str, int] = {}
        for model_data in model_usage.values():
            if not isinstance(model_data, dict):
                continue
            for key in (
                "inputTokens",
                "outputTokens",
                "cacheReadInputTokens",
                "cacheCreationInputTokens",
                "reasoningTokens",
            ):
                value = model_data.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    totals[key] = totals.get(key, 0) + value
        if totals:
            return from_cli(totals, source="claude_cli")
    return None
