"""Model routing logic.

Chooses which model tier to use based on task type.  Call site code should
import TaskType and call resolve_model() rather than hard-coding model names.
"""
from __future__ import annotations

from enum import StrEnum

from app.core.config import settings


class TaskType(StrEnum):
    # reasoning-critical tasks — use strong model
    SEMANTIC_VALIDATION = "semantic_validation"
    REPAIR = "repair"
    REFINEMENT = "refinement"
    # mechanical / structured tasks — use fast model
    IR_CONVERSION = "ir_conversion"
    SUMMARY = "summary"
    SIMPLE_QUERY = "simple_query"


_STRONG_TASKS = {
    TaskType.SEMANTIC_VALIDATION,
    TaskType.REPAIR,
    TaskType.REFINEMENT,
}


def resolve_model(task: TaskType) -> str:
    """Return the appropriate model ID for the given task type."""
    if task in _STRONG_TASKS:
        return settings.llm_strong_model
    return settings.llm_fast_model
