"""Common provider-facing response envelope for every LLM task."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

ResultT = TypeVar("ResultT")


class LlmResponseEnvelope(BaseModel, Generic[ResultT]):
    """A human explanation paired with one task-specific machine result."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        min_length=1,
        description="Concise human-readable explanation of the response.",
    )
    result: ResultT

    @field_validator("description")
    @classmethod
    def _description_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("description must not be blank")
        return stripped
