"""CLI provider tests; no test invokes a real model or spends tokens."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.experiments import ExperimentConfig, ProviderName
from app.llm.providers import cli
from app.llm.providers.claude_cli import ClaudeCliProvider, parse_claude_json
from app.llm.providers.codex_cli import CodexCliProvider, parse_codex_jsonl
from app.llm import registry
from app.llm import client as llm_client
from app.llm.protocol import LlmResponse
from app.llm.tracing import get_traces, reset_trace_context, start_trace_context


class FakeProcess:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0) -> None:
        self.stdout_bytes = stdout.encode()
        self.stderr_bytes = stderr.encode()
        self.final_returncode = returncode
        self.returncode: int | None = None
        self.input: bytes | None = None
        self.terminated = False
        self.killed = False

    async def communicate(self, input: bytes) -> tuple[bytes, bytes]:
        self.input = input
        self.returncode = self.final_returncode
        return self.stdout_bytes, self.stderr_bytes

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode


def patch_process(monkeypatch, process: FakeProcess, on_start=None) -> AsyncMock:
    monkeypatch.setattr(cli.shutil, "which", lambda executable: f"/bin/{executable}")

    async def start(*args, **kwargs):
        if on_start is not None:
            on_start(args, kwargs)
        return process

    factory = AsyncMock(side_effect=start)
    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", factory)
    return factory


CODEX_OUTPUT = "\n".join(
    [
        json.dumps({"type": "thread.started", "thread_id": "t1"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "codex answer"},
            }
        ),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 4,
                    "cached_input_tokens": 2,
                    "reasoning_output_tokens": 3,
                },
            }
        ),
    ]
)


async def test_codex_complete_uses_isolated_system_file_reasoning_and_stdin(monkeypatch):
    process = FakeProcess(CODEX_OUTPUT)
    seen_instructions = {}

    def capture_instructions(args, _kwargs):
        configs = [args[index + 1] for index, value in enumerate(args) if value == "-c"]
        setting = next(value for value in configs if value.startswith("model_instructions_file="))
        path = Path(json.loads(setting.split("=", 1)[1]))
        seen_instructions["path"] = path
        seen_instructions["text"] = path.read_text()

    factory = patch_process(monkeypatch, process, capture_instructions)

    response = await CodexCliProvider("codex", timeout_seconds=7).complete(
        prompt="a prompt with $(not-a-command)",
        system="system",
        model="model;not-shell-input",
        reasoning_effort="high",
    )

    assert response.text == "codex answer"
    assert response.usage is not None
    assert response.usage.total_tokens == 16
    assert response.usage.reasoning_tokens == 3
    assert response.usage.source == "codex_cli"
    args = list(factory.await_args.args)
    assert args[0:2] == ["codex", "exec"]
    assert {"--json", "--ephemeral", "--ignore-user-config", "--ignore-rules"} <= set(args)
    assert args[args.index("--sandbox") + 1] == "read-only"
    configs = [args[index + 1] for index, value in enumerate(args) if value == "-c"]
    assert 'approval_policy="never"' in configs
    assert any(value.startswith("developer_instructions=") for value in configs)
    assert 'model_reasoning_effort="high"' in configs
    assert "a prompt with $(not-a-command)" not in args
    assert "model;not-shell-input" in args
    assert factory.await_args.kwargs["stdin"] is asyncio.subprocess.PIPE
    assert factory.await_args.kwargs["stderr"] is asyncio.subprocess.PIPE
    assert factory.await_args.kwargs.get("shell") is None
    assert process.input is not None
    assert process.input == b"a prompt with $(not-a-command)"
    assert seen_instructions["text"] == "system"
    assert not seen_instructions["path"].exists()
    assert Path(factory.await_args.kwargs["cwd"]).parent == Path("/tmp")


async def test_codex_structured_schema_is_passed_by_temp_file_and_cleaned(monkeypatch):
    process = FakeProcess(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": '{"ok": true}'},
            }
        )
    )
    seen_schema = {}

    def capture_schema(args, _kwargs):
        schema_path = Path(args[args.index("--output-schema") + 1])
        seen_schema.update(json.loads(schema_path.read_text()))

    factory = patch_process(monkeypatch, process, capture_schema)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

    await CodexCliProvider("codex").complete_structured(
        prompt="structured",
        system=None,
        model="gpt-model",
        schema=schema,
    )

    args = list(factory.await_args.args)
    schema_path = Path(args[args.index("--output-schema") + 1])
    assert seen_schema == schema
    assert not schema_path.exists()
    assert not Path(factory.await_args.kwargs["cwd"]).exists()


async def test_claude_structured_uses_json_schema_tools_off_and_maps_model_usage(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "low")
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_THINKING", "1")
    monkeypatch.setenv("MAX_THINKING_TOKENS", "123")
    process = FakeProcess(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "ignored",
                "structured_output": {"ok": True},
                "modelUsage": {
                    "claude-model": {
                        "inputTokens": 20,
                        "outputTokens": 5,
                        "cacheReadInputTokens": 3,
                    }
                },
            }
        )
    )
    factory = patch_process(monkeypatch, process)
    schema = {"type": "object", "required": ["ok"]}

    response = await ClaudeCliProvider("claude").complete_structured(
        prompt="prompt;$(unsafe)",
        system="system",
        model="claude-model",
        schema=schema,
        reasoning_effort="high",
    )

    assert response.text == '{"ok": true}'
    assert response.usage is not None
    assert response.usage.total_tokens == 25
    assert response.usage.source == "claude_cli"
    args = list(factory.await_args.args)
    assert args[:3] == ["claude", "-p", "--output-format"]
    assert "--no-session-persistence" in args
    assert "--safe-mode" in args
    assert args[args.index("--prompt-suggestions") + 1] == "false"
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--effort") + 1] == "high"
    assert json.loads(args[args.index("--json-schema") + 1]) == schema
    assert process.input == b"prompt;$(unsafe)"
    assert "prompt;$(unsafe)" not in args
    assert "--bare" not in args
    environment = factory.await_args.kwargs["env"]
    assert "CLAUDE_CODE_EFFORT_LEVEL" not in environment
    assert "CLAUDE_CODE_DISABLE_THINKING" not in environment
    assert "MAX_THINKING_TOKENS" not in environment


async def test_claude_none_effort_disables_thinking_explicitly(monkeypatch):
    process = FakeProcess(json.dumps({"result": "ok"}))
    factory = patch_process(monkeypatch, process)

    await ClaudeCliProvider("claude").complete(
        prompt="prompt",
        system=None,
        model="claude-model",
        reasoning_effort="none",
    )

    args = list(factory.await_args.args)
    assert "--effort" not in args
    assert factory.await_args.kwargs["env"]["CLAUDE_CODE_DISABLE_THINKING"] == "1"


async def test_history_serialization_preserves_roles_for_both_cli_adapters(monkeypatch):
    for provider, method in [
        (CodexCliProvider("codex"), "complete_with_history"),
        (ClaudeCliProvider("claude"), "complete_with_history"),
    ]:
        process = FakeProcess(CODEX_OUTPUT if isinstance(provider, CodexCliProvider) else json.dumps({"result": "ok"}))
        patch_process(monkeypatch, process)
        await getattr(provider, method)(
            messages=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "second"},
                {"role": "user", "content": "latest"},
            ],
            system="system",
            model="model",
        )
        text = process.input.decode()
        assert text.index("[user 1]") < text.index("[assistant 2]") < text.index("[user 3]")
        assert "first" in text and "second" in text and "latest" in text


@pytest.mark.parametrize("provider", [CodexCliProvider("codex"), ClaudeCliProvider("claude")])
async def test_structured_history_method_returns_json_text(provider, monkeypatch):
    if isinstance(provider, CodexCliProvider):
        output = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": '{"ok": true}'},
            }
        )
    else:
        output = json.dumps(
            {"subtype": "success", "structured_output": {"ok": True}}
        )
    patch_process(monkeypatch, FakeProcess(output))
    response = await provider.complete_structured_with_history(
        messages=[{"role": "user", "content": "latest"}],
        system=None,
        model="model",
        schema={"type": "object"},
    )
    assert json.loads(response.text) == {"ok": True}


def test_history_rejects_unknown_roles():
    with pytest.raises(ValueError, match="unsupported role"):
        cli.history_prompt([{"role": "tool", "content": "no"}])


@pytest.mark.parametrize(
    ("stdout", "error"),
    [
        ("", "no JSON events"),
        ('{"type":"item.completed"}\n', "final agent message"),
        ('{"type": "agent_message", "text": ""}\n', "empty final"),
        ("not json\n", "malformed JSON"),
    ],
)
def test_codex_malformed_or_empty_output_is_clear(stdout, error):
    with pytest.raises(cli.CliOutputError, match=error):
        parse_codex_jsonl(stdout)


async def test_codex_structured_output_must_be_json(monkeypatch):
    patch_process(
        monkeypatch,
        FakeProcess(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "not json"},
                }
            )
        ),
    )
    with pytest.raises(cli.CliOutputError, match="not valid JSON"):
        await CodexCliProvider("codex").complete_structured(
            "prompt", None, "model", {"type": "object"}
        )


def test_claude_malformed_or_missing_output_is_clear():
    with pytest.raises(cli.CliOutputError, match="malformed JSON"):
        parse_claude_json("not json")
    with pytest.raises(cli.CliOutputError, match="structured_output"):
        parse_claude_json(json.dumps({"result": "text"}), structured=True)
    with pytest.raises(cli.CliOutputError, match="empty output"):
        parse_claude_json(json.dumps({"result": ""}))


async def test_missing_executable_nonzero_exit_and_timeout_are_reported(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    with pytest.raises(cli.CliExecutableNotFoundError, match="not found"):
        await CodexCliProvider("missing-codex").complete("p", None, "m")

    failed = FakeProcess("", "permission denied", returncode=2)
    patch_process(monkeypatch, failed)
    with pytest.raises(cli.CliProcessError, match="status 2.*permission denied"):
        await CodexCliProvider("codex").complete("p", None, "m")

    timed_out = FakeProcess("")

    async def never_communicates(input):
        await asyncio.sleep(3600)

    timed_out.communicate = never_communicates
    patch_process(monkeypatch, timed_out)
    with pytest.raises(cli.CliTimeoutError, match="timed out"):
        await CodexCliProvider("codex", timeout_seconds=0.001).complete("p", None, "m")
    assert timed_out.terminated


async def test_cancellation_stops_child_process(monkeypatch):
    process = FakeProcess("")

    async def never_communicates(input):
        await asyncio.sleep(3600)

    process.communicate = never_communicates
    patch_process(monkeypatch, process)
    task = asyncio.create_task(CodexCliProvider("codex", timeout_seconds=30).complete("p", None, "m"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated


def test_provider_and_settings_allow_cli_and_custom_registered_harnesses():
    assert ExperimentConfig(provider_override="codex_cli").provider_override == ProviderName.CODEX_CLI
    assert ExperimentConfig(provider_override="claude_cli").provider_override == ProviderName.CLAUDE_CLI
    assert ExperimentConfig(provider_override="custom_harness").provider_override == "custom_harness"
    settings = Settings(llm_provider="claude_cli", codex_cli_path="codex", claude_cli_path="claude")
    assert settings.llm_provider == ProviderName.CLAUDE_CLI
    assert Settings(llm_provider="custom_harness").llm_provider == "custom_harness"
    with pytest.raises(ValidationError):
        Settings(llm_cli_timeout_seconds=0)


def test_registry_registers_cli_only_when_executable_is_available(monkeypatch):
    original_registry = registry._registry.copy()
    original_unavailable = registry._unavailable.copy()
    try:
        registry._registry.clear()
        registry._unavailable.clear()
        monkeypatch.setattr(
            registry.shutil,
            "which",
            lambda executable: "/bin/found" if executable == "found" else None,
        )
        registry._register_cli(
            "codex_cli", "found", lambda: CodexCliProvider("found")
        )
        registry._register_cli(
            "claude_cli", "missing", lambda: ClaudeCliProvider("missing")
        )
        assert isinstance(registry.get_provider("codex_cli"), CodexCliProvider)
        with pytest.raises(KeyError, match="executable 'missing' was not found"):
            registry.get_provider("claude_cli")
    finally:
        registry._registry.clear()
        registry._registry.update(original_registry)
        registry._unavailable.clear()
        registry._unavailable.update(original_unavailable)


async def test_facade_records_only_unsupported_cli_controls(monkeypatch):
    provider = CodexCliProvider("codex", harness_version="codex-cli 1.2.3")
    provider.complete = AsyncMock(return_value=LlmResponse("ok"))
    monkeypatch.setattr(llm_client, "get_provider", lambda _=None: provider)
    token = start_trace_context()
    try:
        result = await llm_client.complete(
            "prompt",
            provider="codex_cli",
            model="model",
            reasoning_effort="high",
            temperature=0.2,
            seed=7,
        )
        trace = get_traces()[0]
    finally:
        reset_trace_context(token)

    assert result == "ok"
    assert trace.unsupported_controls == ["max_tokens", "temperature", "seed"]
    assert trace.provider_version == "codex-cli 1.2.3"
    assert trace.reasoning_effort == "high"
    assert trace.effective_reasoning_effort == "high"
    assert trace.effective_max_tokens is None
    assert provider.complete.await_args.kwargs["reasoning_effort"] == "high"


def test_cli_version_reads_one_line_without_starting_a_model(monkeypatch):
    result = type(
        "VersionResult",
        (),
        {"returncode": 0, "stdout": "codex-cli 1.2.3\n", "stderr": ""},
    )()
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: result)

    assert cli.cli_version("codex") == "codex-cli 1.2.3"
