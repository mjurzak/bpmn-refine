"""Model and provider routing.

Call sites import TaskType and use resolve_model() / resolve_provider() rather
than hard-coding names.  Both respect per-tier overrides from settings.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.core.config import settings
from app.experiments import ExperimentConfig, ModelTier


class TaskType(StrEnum):
    # reasoning-critical tasks — use strong tier
    SEMANTIC_VALIDATION = "semantic_validation"
    REPAIR = "repair"
    REFINEMENT = "refinement"
    # mechanical / structured tasks — use fast tier
    IR_CONVERSION = "ir_conversion"
    SUMMARY = "summary"
    SIMPLE_QUERY = "simple_query"


_STRONG_TASKS = {
    TaskType.SEMANTIC_VALIDATION,
    TaskType.REPAIR,
    TaskType.REFINEMENT,
}


def resolve_sampling(config: ExperimentConfig | None = None) -> dict[str, Any]:
    """the sampling controls a call should ask its provider for

    `ExperimentConfig` has declared `temperature` and `seed` since the schema was
    written, but nothing read them, so every run was executed at whatever default
    the provider chose while the run record named a temperature. Call sites spread
    this into their client call; the facade then records which controls the
    selected provider could actually forward.
    """
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
    """Return the provider name for the given task type.

    Checks per-tier overrides first; falls back to the global llm_provider.
    """
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
