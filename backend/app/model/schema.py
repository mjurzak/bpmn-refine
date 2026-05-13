"""Pydantic data models for the BPMN diagram domain.

This is the canonical in-memory representation used by the default converter.
Every piece of information needed to reconstruct valid BPMN XML must be present
here — the round-trip property (XML -> model -> XML) must hold.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class FlowNodeType(StrEnum):
    START_EVENT = "startEvent"
    END_EVENT = "endEvent"
    INTERMEDIATE_CATCH_EVENT = "intermediateCatchEvent"
    INTERMEDIATE_THROW_EVENT = "intermediateThrowEvent"
    TASK = "task"
    USER_TASK = "userTask"
    SERVICE_TASK = "serviceTask"
    SCRIPT_TASK = "scriptTask"
    SEND_TASK = "sendTask"
    RECEIVE_TASK = "receiveTask"
    MANUAL_TASK = "manualTask"
    CALL_ACTIVITY = "callActivity"
    SUB_PROCESS = "subProcess"
    EXCLUSIVE_GATEWAY = "exclusiveGateway"
    INCLUSIVE_GATEWAY = "inclusiveGateway"
    PARALLEL_GATEWAY = "parallelGateway"
    EVENT_BASED_GATEWAY = "eventBasedGateway"
    COMPLEX_GATEWAY = "complexGateway"


class FlowNode(BaseModel):
    id: str
    type: FlowNodeType
    name: str | None = None
    # outgoing/incoming are edge IDs — filled in by the converter
    outgoing: list[str] = Field(default_factory=list)
    incoming: list[str] = Field(default_factory=list)
    # raw attributes preserved for round-trip
    extra: dict = Field(default_factory=dict)


class SequenceFlow(BaseModel):
    id: str
    source_ref: str
    target_ref: str
    name: str | None = None
    condition_expression: str | None = None


class Lane(BaseModel):
    id: str
    name: str | None = None
    flow_node_refs: list[str] = Field(default_factory=list)


class Pool(BaseModel):
    id: str
    name: str | None = None
    lanes: list[Lane] = Field(default_factory=list)


class BpmnProcess(BaseModel):
    id: str
    name: str | None = None
    is_executable: bool = False
    flow_nodes: list[FlowNode] = Field(default_factory=list)
    sequence_flows: list[SequenceFlow] = Field(default_factory=list)
    pools: list[Pool] = Field(default_factory=list)


class BpmnDiagram(BaseModel):
    """Top-level model for a single BPMN diagram."""

    definitions_id: str
    target_namespace: str = "http://bpmn.io/schema/bpmn"
    processes: list[BpmnProcess] = Field(default_factory=list)
    # raw XML namespaces preserved for round-trip
    namespaces: dict[str, str] = Field(default_factory=dict)
