"""Shared, isolated subprocess support for local LLM CLI harnesses."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path


class CliProviderError(RuntimeError):
    """Base error for a CLI provider failure."""


class CliExecutableNotFoundError(CliProviderError):
    """The configured executable is not available on PATH."""


class CliTimeoutError(CliProviderError):
    """The CLI process did not finish before the configured timeout."""


class CliProcessError(CliProviderError):
    """The CLI process exited unsuccessfully."""


class CliOutputError(CliProviderError):
    """The CLI returned output that cannot be interpreted."""


def executable_available(executable: str) -> bool:
    """Return whether an executable path or PATH name is runnable."""
    return shutil.which(executable) is not None


def cli_version(executable: str, timeout_seconds: float = 5.0) -> str:
    """Read a harness version without invoking a model or a shell."""
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    output = result.stdout.strip() or result.stderr.strip()
    if result.returncode != 0 or not output:
        return "unknown"
    return output.splitlines()[0][:200]


def history_prompt(messages: list[dict]) -> str:
    """Serialize history deterministically for CLIs without a messages API."""
    parts = ["=== BEGIN BPMN LLM HISTORY ==="]
    for index, message in enumerate(messages, start=1):
        role = message.get("role")
        if role not in {"user", "assistant"}:
            raise ValueError(f"history message {index} has unsupported role {role!r}")
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError(f"history message {index} content must be a string")
        parts.extend([f"[{role} {index}]", content])
    parts.extend(
        [
            "=== END BPMN LLM HISTORY ===",
            "Answer the latest user message. Do not use tools or modify files.",
        ]
    )
    return "\n".join(parts)


async def run_cli(
    argv: Sequence[str],
    prompt: str,
    timeout_seconds: float,
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Run one CLI invocation with stdin/stdout/stderr separated and no shell."""
    executable = str(argv[0])
    if not executable_available(executable):
        raise CliExecutableNotFoundError(
            f"LLM CLI executable {executable!r} was not found; "
            "install the CLI or set its *_CLI_PATH setting"
        )

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.fspath(cwd),
            env=dict(env) if env is not None else None,
            start_new_session=os.name == "posix",
        )
    except FileNotFoundError as exc:
        raise CliExecutableNotFoundError(
            f"LLM CLI executable {executable!r} could not be started"
        ) from exc
    except OSError as exc:
        raise CliProviderError(
            f"could not start LLM CLI {executable!r}: {exc}"
        ) from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(prompt.encode("utf-8")), timeout=timeout_seconds
        )
    except asyncio.TimeoutError as exc:
        await _stop_process(process)
        raise CliTimeoutError(
            f"LLM CLI {executable!r} timed out after {timeout_seconds:g}s"
        ) from exc
    except asyncio.CancelledError:
        await _stop_process(process)
        raise

    stdout_text = _decode(stdout, "stdout")
    stderr_text = _decode(stderr, "stderr")
    if process.returncode:
        detail = stderr_text.strip() or stdout_text.strip() or "no diagnostic output"
        raise CliProcessError(
            f"LLM CLI {executable!r} exited with status {process.returncode}: "
            f"{detail[:2000]}"
        )
    if not stdout_text.strip():
        raise CliOutputError(
            f"LLM CLI {executable!r} returned empty stdout"
            + (f"; stderr: {stderr_text.strip()[:1000]}" if stderr_text.strip() else "")
        )
    return stdout_text, stderr_text


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        _signal_process(process, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
        return
    except (asyncio.TimeoutError, ProcessLookupError):
        pass
    try:
        _signal_process(process, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        await process.wait()
    except ProcessLookupError:
        pass


def _signal_process(process: asyncio.subprocess.Process, sig: int) -> None:
    """Stop the CLI and its descendants when the platform supports process groups."""
    pid = getattr(process, "pid", None)
    if os.name == "posix" and isinstance(pid, int):
        try:
            os.killpg(pid, sig)
            return
        except ProcessLookupError:
            raise
        except OSError:
            pass
    if sig == signal.SIGKILL:
        process.kill()
    else:
        process.terminate()


def _decode(value: bytes, stream: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CliOutputError(f"LLM CLI returned non-UTF-8 {stream}") from exc


class IsolatedCliCall:
    """Create a disposable working directory for one request."""

    def __init__(self) -> None:
        self._directory: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path:
        self._directory = tempfile.TemporaryDirectory(prefix="bpmn-ai-cli-")
        return Path(self._directory.name)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._directory is not None:
            self._directory.cleanup()
