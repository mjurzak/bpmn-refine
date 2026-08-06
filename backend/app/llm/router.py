"""Model and provider routing by task type, respecting per-tier overrides from settings."""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.core.config import settings
from app.experiments import ExperimentConfig, ModelTier


class TaskType(StrEnum):
    # reasoning-critical tasks, strong tier
    SEMANTIC_VALIDATION = "semantic_validation"
    REPAIR = "repair"
    REFINEMENT = "refinement"
    # mechanical / structured tasks, fast tier
    IR_CONVERSION = "ir_conversion"
    SUMMARY = "summary"
    SIMPLE_QUERY = "simple_query"


_STRONG_TASKS = {
    TaskType.SEMANTIC_VALIDATION,
    TaskType.REPAIR,
    TaskType.REFINEMENT,
}


def resolve_sampling(config: ExperimentConfig | None = None) -> dict[str, Any]:
    """the sampling controls a call should ask its provider for, spread into the client by call sites"""
    if config is None:
        return {}
    controls: dict[str, Any] = {}
    if config.temperature is not None:
        controls["temperature"] = config.temperature
    if config.seed is not None:
        controls["seed"] = config.seed
    return controls


def resolve_model(task: TaskType, config: ExperimentConfig | None = None) -> str:
    """Return the model name for the given task type."""
    if config is not None:
        if config.model_tier == ModelTier.CUSTOM:
            return config.model_override or settings.llm_fast_model
        if config.model_tier == ModelTier.STRONG:
            return settings.llm_strong_model
        return settings.llm_fast_model

    if task in _STRONG_TASKS:
        return settings.llm_strong_model
    return settings.llm_fast_model


def resolve_provider(task: TaskType, config: ExperimentConfig | None = None) -> str:
    """Return the provider for the given task type, preferring per-tier overrides over llm_provider."""
    if config is not None:
        if config.provider_override:
            return config.provider_override
        if config.model_tier == ModelTier.STRONG:
            return settings.llm_strong_provider or settings.llm_provider
        if config.model_tier == ModelTier.FAST:
            return settings.llm_fast_provider or settings.llm_provider

    if task in _STRONG_TASKS:
        return settings.llm_strong_provider or settings.llm_provider
    return settings.llm_fast_provider or settings.llm_provider
