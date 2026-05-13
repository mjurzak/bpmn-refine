"""Experiment and run envelope schemas."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

HASH_PREFIX_LENGTH = 12


class ModelTier(StrEnum):
    STRONG = "strong"
    FAST = "fast"
    CUSTOM = "custom"


class ProviderName(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OLLAMA = "ollama"


class IrFormat(StrEnum):
    PYDANTIC = "pydantic"
    PYDANTIC_JSON = "pydantic_json"
    YAML = "yaml"
    MERMAID = "mermaid"
    COMPACT_JSON = "compact_json"


class T2Tool(StrEnum):
    BPMN_ANALYZER = "bpmn_analyzer"
    WOFLAN = "woflan"
    BPMNSPECTOR = "bpmnspector"


class RepairMode(StrEnum):
    ATOMIC = "atomic"
    REGEN = "regen"


class TiersEnabled(BaseModel):
    t1: bool = True
    t2: bool = False
    t3: bool = False


class ExperimentConfig(BaseModel):
    model_tier: ModelTier = ModelTier.STRONG
    model_override: str | None = None
    provider_override: ProviderName | None = None
    ir_format: IrFormat = IrFormat.PYDANTIC
    tiers_enabled: TiersEnabled = Field(default_factory=TiersEnabled)
    t2_tools: list[T2Tool] = Field(default_factory=lambda: list(T2Tool))
    repair_mode: RepairMode = RepairMode.ATOMIC
    max_repair_iters: int = Field(default=5, ge=1)
    temperature: float = Field(default=0.0, ge=0.0)
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


class RunBlock(BaseModel):
    model_used: str
    prompt_versions: dict[str, PromptVersion] = Field(default_factory=dict)
    converter: str
    rules_version: str
    checkers: dict[str, str] | None = None
    config_hash: str = Field(min_length=12, max_length=12)
    timestamp: datetime
    request_id: str
    iterations: int | None = Field(default=None, ge=0)
    converged: bool | None = None


class RunEnvelope(BaseModel):
    run: RunBlock


def build_run_block(
    *,
    config: ExperimentConfig,
    model_used: str,
    converter: str,
    rules_version: str,
    prompt_files: Mapping[str, Path] | None = None,
    checkers: dict[str, str] | None = None,
    request_id: str | None = None,
    timestamp: datetime | None = None,
    iterations: int | None = None,
    converged: bool | None = None,
) -> RunBlock:
    return RunBlock(
        model_used=model_used,
        prompt_versions={
            name: prompt_version(path) for name, path in (prompt_files or {}).items()
        },
        converter=converter,
        rules_version=rules_version,
        checkers=checkers,
        config_hash=config_hash(config),
        timestamp=timestamp or datetime.now(UTC),
        request_id=request_id or str(uuid4()),
        iterations=iterations,
        converged=converged,
    )
