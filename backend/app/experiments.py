"""Experiment and run envelope schemas."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

HASH_PREFIX_LENGTH = 12
CONVERTER_VERSION = "pydantic_ir@v1"
IR_FORMAT_CONVERTER_VERSIONS = {
    "pydantic": CONVERTER_VERSION,
    "pydantic_json": "pydantic_json@v1",
    "yaml": "yaml@v1",
    "mermaid": "mermaid@v1",
    "compact_json": "compact_json@v1",
}


class ModelTier(StrEnum):
    STRONG = "strong"
    FAST = "fast"
    CUSTOM = "custom"


class ProviderName(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OLLAMA = "ollama"
    GEMINI = "gemini"
    CODEX_CLI = "codex_cli"
    CLAUDE_CLI = "claude_cli"


class IrFormat(StrEnum):
    PYDANTIC = "pydantic"
    PYDANTIC_JSON = "pydantic_json"
    YAML = "yaml"
    MERMAID = "mermaid"
    COMPACT_JSON = "compact_json"


class RepairMode(StrEnum):
    ATOMIC = "atomic"
    REGEN = "regen"


class ReasoningEffort(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class LlmValidationScope(StrEnum):
    """The defect taxonomy used by the optional tier-3 validator."""

    SEMANTIC = "semantic"
    HOLISTIC = "holistic"


class TiersEnabled(BaseModel):
    t1: bool = True
    t2: bool = False
    t3: bool = False


class ExperimentConfig(BaseModel):
    model_tier: ModelTier = ModelTier.STRONG
    model_override: str | None = None
    # Built-ins use ProviderName constants, while custom registered adapters remain valid.
    provider_override: str | None = None
    ir_format: IrFormat = IrFormat.PYDANTIC
    tiers_enabled: TiersEnabled = Field(default_factory=TiersEnabled)
    include_formal_evidence: bool = True
    include_reference_description: bool = True
    include_semantic_projection: bool = False
    llm_validation_scope: LlmValidationScope = LlmValidationScope.SEMANTIC
    repair_mode: RepairMode = RepairMode.ATOMIC
    max_repair_iters: int = Field(default=5, ge=1)
    temperature: float | None = Field(default=None, ge=0.0)
    reasoning_effort: ReasoningEffort | None = None
    seed: int | None = None
    experiment_id: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _custom_model_requires_override(self) -> Self:
        if self.model_tier == ModelTier.CUSTOM and not self.model_override:
            raise ValueError("model_override is required when model_tier is custom")
        return self


def canonical_config_json(config: ExperimentConfig) -> str:
    data = _drop_nulls(config.model_dump(mode="json"))
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def config_hash(config: ExperimentConfig) -> str:
    encoded = canonical_config_json(config).encode("utf-8")
    return hash_bytes(encoded)


def converter_version(config: ExperimentConfig) -> str:
    return IR_FORMAT_CONVERTER_VERSIONS[str(config.ir_format)]


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:HASH_PREFIX_LENGTH]


def _drop_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_nulls(item) for item in value]
    return value


class PromptVersion(BaseModel):
    name: str
    hash: str = Field(min_length=12, max_length=12)


def prompt_version(path: Path) -> PromptVersion:
    return PromptVersion(name=path.name, hash=hash_bytes(path.read_bytes()))


def app_commit() -> str:
    """the implementation revision this process is running"""
    override = os.environ.get("BPMN_AI_APP_COMMIT")
    if override:
        return override
    return _git_commit()


@lru_cache(maxsize=1)
def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent.parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else "unknown"


class RunBlock(BaseModel):
    # what was actually called, "none" when nothing was
    model_used: str
    # what the configuration resolved to, called or not
    model_configured: str | None = None
    prompt_versions: dict[str, PromptVersion] = Field(default_factory=dict)
    converter: str
    rules_version: str
    checkers: dict[str, str] | None = None
    config_hash: str = Field(min_length=12, max_length=12)
    config: ExperimentConfig | None = None
    app_commit: str | None = None
    timestamp: datetime
    request_id: str
    iterations: int | None = Field(default=None, ge=0)
    converged: bool | None = None


def build_run_block(
    *,
    config: ExperimentConfig,
    model_used: str,
    converter: str,
    rules_version: str,
    model_configured: str | None = None,
    prompt_files: Mapping[str, Path] | None = None,
    checkers: dict[str, str] | None = None,
    request_id: str | None = None,
    timestamp: datetime | None = None,
    iterations: int | None = None,
    converged: bool | None = None,
) -> RunBlock:
    return RunBlock(
        model_used=model_used,
        model_configured=model_configured,
        prompt_versions={
            name: prompt_version(path) for name, path in (prompt_files or {}).items()
        },
        converter=converter,
        rules_version=rules_version,
        checkers=checkers,
        config_hash=config_hash(config),
        config=config,
        app_commit=app_commit(),
        timestamp=timestamp or datetime.now(UTC),
        request_id=request_id or str(uuid4()),
        iterations=iterations,
        converged=converged,
    )
