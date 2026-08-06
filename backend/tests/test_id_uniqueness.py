"""Diagram-scoped id uniqueness and the LLM correction round it drives."""

import json
import re

import pytest
import yaml
from pydantic import ValidationError

from app.experiments import ExperimentConfig, IrFormat
from app.model.registry import get_converter
from app.model.schema import BpmnDiagram
from app.services.ir_payload import (
    IR_CORRECTION_ATTEMPTS,
    call_with_ir_correction,
    parse_diagram_payload,
)


def _diagram_dict(flow_nodes=None, processes=None):
    return {
        "definitions_id": "d1",
        "target_namespace": "http://example.com",
        "namespaces": {},
        "processes": processes
        or [
            {
                "id": "p1",
                "name": None,
                "is_executable": True,
                "pools": [],
                "flow_nodes": flow_nodes if flow_nodes is not None else [_START, _TASK],
                "sequence_flows": [
                    {
                        "id": "f1",
                        "source_ref": "s",
                        "target_ref": "t",
                        "name": None,
                        "condition_expression": None,
                    }
                ],
            }
        ],
    }


_START = {
    "id": "s",
    "type": "startEvent",
    "name": "Start",
    "incoming": [],
    "outgoing": ["f1"],
    "extra": {},
}
_TASK = {
    "id": "t",
    "type": "task",
    "name": "Task",
    "incoming": ["f1"],
    "outgoing": [],
    "extra": {},
}


def test_valid_diagram_is_accepted():
    diagram = BpmnDiagram.model_validate(_diagram_dict())
    assert [node.id for node in diagram.processes[0].flow_nodes] == ["s", "t"]


def test_duplicate_node_ids_rejected():
    duplicate = dict(_TASK, name="Shadow")
    with pytest.raises(ValidationError, match="Duplicate element ID"):
        BpmnDiagram.model_validate(_diagram_dict([_START, _TASK, duplicate]))


def test_duplicate_across_processes_rejected():
    """The XML parser scoped uniqueness per process and never caught this."""
    processes = _diagram_dict()["processes"] + [
        {
            "id": "p2",
            "name": None,
            "is_executable": True,
            "pools": [],
            "flow_nodes": [dict(_TASK, name="Shadow", incoming=[], outgoing=[])],
            "sequence_flows": [],
        }
    ]
    with pytest.raises(ValidationError, match="Duplicate element ID"):
        BpmnDiagram.model_validate(_diagram_dict(processes=processes))


def test_process_id_colliding_with_node_id_rejected():
    collides = dict(_TASK, id="p1", name="Collides", incoming=[], outgoing=[])
    with pytest.raises(ValidationError, match="Duplicate element ID"):
        BpmnDiagram.model_validate(_diagram_dict([_START, _TASK, collides]))


def test_error_names_the_offending_ids():
    """The message is fed back to the LLM verbatim, so it has to be specific."""
    duplicate = dict(_TASK, name="Shadow")
    with pytest.raises(ValidationError) as excinfo:
        BpmnDiagram.model_validate(_diagram_dict([_START, _TASK, duplicate]))
    assert "'t'" in str(excinfo.value)


@pytest.mark.parametrize("ir_format", list(IrFormat))
def test_every_ir_format_rejects_duplicates(ir_format):
    """A duplicate injected into serialized text must not survive the round trip."""
    base = BpmnDiagram.model_validate(_diagram_dict())
    converter = get_converter(str(ir_format))
    text = converter.serialize(base).decode()

    if ir_format is IrFormat.PYDANTIC:
        # the canonical converter is XML-backed, so duplicate the element itself
        match = re.search(r'<task id="t".*?</task>', text, re.DOTALL)
        assert match, "expected a task element to duplicate"
        element = match.group(0)
        mutated = text.replace(element, f"{element}{element}", 1).encode()
    elif ir_format is IrFormat.MERMAID:
        head, rest = text.split("bpmn-ai-ir:", 1)
        payload, tail = rest.split("\n", 1)
        data = json.loads(payload)
        data["p"][0]["v"].append(dict(data["p"][0]["v"][-1]))
        mutated = f"{head}bpmn-ai-ir:{json.dumps(data)}\n{tail}".encode()
    elif ir_format is IrFormat.YAML:
        data = yaml.safe_load(text)
        data["processes"][0]["flow_nodes"].append(
            dict(data["processes"][0]["flow_nodes"][-1])
        )
        mutated = yaml.safe_dump(data).encode()
    elif ir_format is IrFormat.COMPACT_JSON:
        data = json.loads(text)
        data["p"][0]["v"].append(dict(data["p"][0]["v"][-1]))
        mutated = json.dumps(data).encode()
    else:
        data = json.loads(text)
        data["processes"][0]["flow_nodes"].append(
            dict(data["processes"][0]["flow_nodes"][-1])
        )
        mutated = json.dumps(data).encode()

    with pytest.raises(ValueError, match="Duplicate element ID"):
        converter.parse(mutated)


def test_llm_payload_path_rejects_duplicates():
    """The regen path parses through here."""
    duplicate = dict(_TASK, name="Shadow")
    with pytest.raises(ValidationError, match="Duplicate element ID"):
        parse_diagram_payload(
            _diagram_dict([_START, _TASK, duplicate]), ExperimentConfig()
        )


# ---------------------------------------------------------------------------
# correction round
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_correction_round_recovers_from_a_bad_first_answer():
    calls: list[str | None] = []

    async def attempt(feedback):
        calls.append(feedback)
        nodes = [_START, _TASK] if feedback else [_START, _TASK, dict(_TASK)]
        return BpmnDiagram.model_validate(_diagram_dict(nodes))

    diagram = await call_with_ir_correction(attempt)

    assert len(calls) == 2
    assert calls[0] is None
    assert "Duplicate element ID" in calls[1]
    assert [node.id for node in diagram.processes[0].flow_nodes] == ["s", "t"]


@pytest.mark.anyio
async def test_a_good_first_answer_is_not_retried():
    calls: list[str | None] = []

    async def attempt(feedback):
        calls.append(feedback)
        return BpmnDiagram.model_validate(_diagram_dict())

    await call_with_ir_correction(attempt)

    assert calls == [None]


@pytest.mark.anyio
async def test_correction_gives_up_and_surfaces_the_defect():
    calls: list[str | None] = []

    async def attempt(feedback):
        calls.append(feedback)
        return BpmnDiagram.model_validate(_diagram_dict([_START, _TASK, dict(_TASK)]))

    with pytest.raises(ValidationError, match="Duplicate element ID"):
        await call_with_ir_correction(attempt)

    assert len(calls) == IR_CORRECTION_ATTEMPTS


@pytest.mark.anyio
async def test_transport_failures_are_not_retried():
    """Re-prompting a timeout with "your diagram was rejected" makes no sense."""
    calls: list[str | None] = []

    class ProviderTimeout(Exception):
        pass

    async def attempt(feedback):
        calls.append(feedback)
        raise ProviderTimeout("upstream timed out")

    with pytest.raises(ProviderTimeout):
        await call_with_ir_correction(attempt)

    assert calls == [None]
