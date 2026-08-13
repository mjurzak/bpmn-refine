"""Codex CLI provider using the installed subscription-authenticated harness."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.llm.protocol import LlmResponse
from app.llm.providers.cli import (
    CliOutputError,
    IsolatedCliCall,
    history_prompt,
    run_cli,
)
from app.llm.usage import from_cli

_HARNESS_INSTRUCTIONS = (
    "Act as a text-completion provider. Answer the request directly. "
    "Do not call tools, inspect files, or modify files."
)


class CodexCliProvider:
    supports_temperature = False
    supports_seed = False
    supports_reasoning_effort = True
    supports_max_tokens = False

    def __init__(
        self,
        executable: str = "codex",
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
        return await self._complete(
            prompt, system, model, reasoning_effort, schema=schema
        )

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
            history_prompt(messages), system, model, reasoning_effort, schema=schema
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
                "exec",
                "--json",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "-c",
                'approval_policy="never"',
                "-c",
                f"developer_instructions={json.dumps(_HARNESS_INSTRUCTIONS)}",
                "--skip-git-repo-check",
                "-C",
                str(cwd),
                "-m",
                model,
            ]
            if system:
                instructions_path = cwd / "model-instructions.md"
                instructions_path.write_text(system, encoding="utf-8")
                args.extend(
                    ["-c", f"model_instructions_file={json.dumps(str(instructions_path))}"]
                )
            if reasoning_effort and reasoning_effort != "none":
                args.extend(
                    ["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"]
                )
            if schema is not None:
                schema_path = cwd / "output-schema.json"
                schema_path.write_text(
                    json.dumps(schema, ensure_ascii=False), encoding="utf-8"
                )
                args.extend(["--output-schema", str(schema_path)])
            stdout, _ = await run_cli(
                args, prompt, self.timeout_seconds, cwd=cwd
            )
        response = parse_codex_jsonl(stdout)
        if schema is not None:
            try:
                json.loads(response.text)
            except json.JSONDecodeError as exc:
                raise CliOutputError(
                    f"Codex CLI structured output was not valid JSON: {exc.msg}"
                ) from exc
        return response


def parse_codex_jsonl(stdout: str) -> LlmResponse:
    answer: str | None = None
    usage_payload: dict[str, Any] | None = None
    events = [line for line in stdout.splitlines() if line.strip()]
    if not events:
        raise CliOutputError("Codex CLI returned no JSON events")
    for line_number, line in enumerate(events, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CliOutputError(
                f"Codex CLI emitted malformed JSON on line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(event, dict):
            raise CliOutputError("Codex CLI emitted a non-object JSON event")
        event_type = event.get("type")
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            if isinstance(item.get("text"), str):
                answer = item["text"]
        elif event_type in {"agent_message", "message"} and isinstance(
            event.get("text"), str
        ):
            answer = event["text"]
        if event_type in {"turn.completed", "response.completed"}:
            usage = event.get("usage")
            if isinstance(usage, dict):
                usage_payload = usage
        if event_type in {"turn.failed", "error"}:
            detail = event.get("error") or event.get("message") or event_type
            raise CliOutputError(f"Codex CLI reported a failed turn: {detail}")
    if answer is None:
        raise CliOutputError("Codex CLI JSON did not contain a final agent message")
    if not answer.strip():
        raise CliOutputError("Codex CLI returned an empty final agent message")
    return LlmResponse(answer, from_cli(usage_payload, source="codex_cli"))
