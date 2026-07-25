"""LLM-facing diagram payload serialisation helpers."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.experiments import ExperimentConfig, IrFormat
from app.model.registry import get_converter
from app.model.schema import BpmnDiagram

logger = logging.getLogger(__name__)

_JSON_FENCES = {IrFormat.PYDANTIC, IrFormat.PYDANTIC_JSON, IrFormat.COMPACT_JSON}
_FENCED_BLOCK = re.compile(r"```(?P<label>[^\s`]*)[ \t]*\n[\s\S]*?```")

# one initial attempt plus one correction round. a second correction almost never
# succeeds where the first failed, and every round costs the user a full latency
# budget, so the ceiling stays low
IR_CORRECTION_ATTEMPTS = 2

# errors that mean "the model produced bad content" and are therefore worth handing
# back to it. transport and provider failures are not — re-prompting a timeout with
# "the diagram you returned was rejected" is nonsense. pydantic's ValidationError and
# json.JSONDecodeError are both ValueError subclasses, so they are covered here.
_CORRECTABLE_ERRORS = (ValueError, KeyError, TypeError)

T = TypeVar("T")


def diagram_payload(diagram: BpmnDiagram, config: ExperimentConfig | None) -> Any:
    """Return the diagram payload matching the selected experiment IR format."""
    active_config = config or ExperimentConfig()
    if active_config.ir_format == IrFormat.PYDANTIC:
        return diagram.model_dump(mode="json")
    return diagram_payload_text(diagram, active_config)


def diagram_payload_text(diagram: BpmnDiagram, config: ExperimentConfig | None) -> str:
    """Return a text form suitable for fenced prompt context blocks."""
    active_config = config or ExperimentConfig()
    if active_config.ir_format == IrFormat.PYDANTIC:
        return diagram.model_dump_json(indent=2)
    return _candidate_converter(active_config).serialize(diagram).decode("utf-8")


def parse_diagram_payload(payload: Any, config: ExperimentConfig | None) -> BpmnDiagram:
    """Parse an LLM-emitted diagram payload using the selected IR format."""
    active_config = config or ExperimentConfig()
    if active_config.ir_format == IrFormat.PYDANTIC:
        if isinstance(payload, str):
            return BpmnDiagram.model_validate_json(payload)
        return BpmnDiagram.model_validate(payload)
    if isinstance(payload, bytes):
        encoded = payload
    elif isinstance(payload, str):
        encoded = payload.encode("utf-8")
    else:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return _candidate_converter(active_config).parse(encoded)


def parse_diagram_from_fenced_reply(
    reply: str,
    config: ExperimentConfig | None,
) -> BpmnDiagram | None:
    """Extract and parse the first compatible fenced diagram block from a reply."""
    last_error: Exception | None = None
    for fence in _candidate_fences(config):
        opening = f"```{fence}"
        if opening not in reply:
            continue
        start = reply.index(opening) + len(opening)
        end = reply.index("```", start)
        try:
            return parse_diagram_payload(reply[start:end].strip(), config)
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return None


def strip_diagram_from_fenced_reply(
    reply: str,
    config: ExperimentConfig | None,
) -> str:
    """Remove machine-readable diagram blocks from a user-facing reply."""
    compatible_fences = set(_candidate_fences(config))
    return _FENCED_BLOCK.sub(
        lambda match: "" if match.group("label") in compatible_fences else match.group(0),
        reply,
    ).strip()


async def call_with_ir_correction(
    attempt: Callable[[str | None], Awaitable[T]],
    *,
    max_attempts: int = IR_CORRECTION_ATTEMPTS,
) -> T:
    """Run an LLM call that yields a diagram, letting the model fix its own output.

    A model can return something well-formed for its IR format that still violates
    a diagram invariant — duplicate element IDs being the case that motivated this.
    Neither available response is good on its own: failing the turn costs the user
    their request, and silently accepting the payload corrupts the session, since
    the diagram exports but can no longer be re-parsed. So the rejection is handed
    back as feedback and the model is asked to reissue.

    `attempt` receives `None` on the first call and correction text afterwards; it
    is responsible for both the LLM call and the parse, so that whatever it raises
    describes the actual defect. Only content errors are retried
    (see `_CORRECTABLE_ERRORS`); transport failures propagate untouched.
    """
    feedback: str | None = None
    for number in range(1, max_attempts + 1):
        try:
            return await attempt(feedback)
        except _CORRECTABLE_ERRORS as exc:
            if number == max_attempts:
                logger.warning(
                    "LLM diagram output still invalid after %d attempt(s): %s",
                    max_attempts,
                    exc,
                )
                raise
            logger.info(
                "LLM diagram output rejected on attempt %d/%d, asking for a correction: %s",
                number,
                max_attempts,
                exc,
            )
            feedback = ir_correction_feedback(exc)
    raise AssertionError("unreachable: loop either returns or raises")


def ir_correction_feedback(error: Exception) -> str:
    """Turn a rejection into an instruction the model can act on."""
    return (
        "Your previous response was rejected before it could be applied.\n\n"
        f"Reason: {error}\n\n"
        "Reissue the complete diagram in the same format with this problem fixed. "
        "Change nothing else, and do not explain the correction."
    )


def diagram_fence(config: ExperimentConfig | None) -> str:
    active_config = config or ExperimentConfig()
    if active_config.ir_format in _JSON_FENCES:
        return "json"
    return str(active_config.ir_format)


def _candidate_converter(config: ExperimentConfig):
    return get_converter(str(config.ir_format))


def _candidate_fences(config: ExperimentConfig | None) -> list[str]:
    active_config = config or ExperimentConfig()
    preferred = [diagram_fence(active_config), str(active_config.ir_format)]
    fallbacks = ["diagram", "ir"]
    fences: list[str] = []
    for fence in preferred + fallbacks:
        if fence not in fences:
            fences.append(fence)
    return fences
