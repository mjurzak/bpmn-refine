"""Pydantic data models for the BPMN diagram domain.

This is the canonical in-memory representation used by the default converter.
Every piece of information needed to reconstruct valid BPMN XML must be present
here — the round-trip property (XML -> model -> XML) must hold.
"""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Self, TypeAlias

from pydantic import BaseModel, Field, model_validator


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


# Custom type aliases. These remain strings at runtime; Pydantic enforces the
# surrounding model shape rather than distinct ID wrapper classes.
FlowNodeId: TypeAlias = str
SequenceFlowId: TypeAlias = str


class Bounds(BaseModel):
    """A BPMNDI shape rectangle, in diagram coordinates."""

    x: float
    y: float
    width: float
    height: float


class Waypoint(BaseModel):
    """One bend point on a BPMNDI edge."""

    x: float
    y: float


class FlowNode(BaseModel):
    id: FlowNodeId
    type: FlowNodeType
    name: str | None = None
    # outgoing/incoming are edge IDs — filled in by the converter
    outgoing: list[SequenceFlowId] = Field(default_factory=list)
    incoming: list[SequenceFlowId] = Field(default_factory=list)
    # where the author put this node. None means "never had a shape" (a node the
    # LLM just added), which the serialiser fills in from the fallback layout
    bounds: Bounds | None = None
    label_bounds: Bounds | None = None
    # raw attributes preserved for round-trip
    extra: dict = Field(default_factory=dict)


class SequenceFlow(BaseModel):
    id: SequenceFlowId
    source_ref: FlowNodeId
    target_ref: FlowNodeId
    name: str | None = None
    condition_expression: str | None = None
    # the author's routing. an empty list means "no edge shape", not "straight"
    waypoints: list[Waypoint] = Field(default_factory=list)
    label_bounds: Bounds | None = None


class Lane(BaseModel):
    id: str
    name: str | None = None
    flow_node_refs: list[FlowNodeId] = Field(default_factory=list)


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

    @model_validator(mode="after")
    def _reject_duplicate_ids(self) -> Self:
        """Enforce document-scoped ID uniqueness, as BPMN 2.0 types `id` as xsd:ID.

        This lives on the model rather than in the XML parser on purpose. Every
        input path — the five IR formats, LLM-authored payloads, edit-op results —
        builds a `BpmnDiagram`, so this is the one place that covers all of them.
        A parser-level check only ever guarded uploaded files.

        Uniqueness is not a cosmetic concern here: `_Graph` keys its adjacency
        index by node ID, so a duplicate silently shadows its twin and the
        reachability rules go on to read the wrong node's edges.
        """
        duplicates = [
            element_id
            for element_id, count in Counter(self._element_ids()).items()
            if count > 1
        ]
        if duplicates:
            listed = ", ".join(f"'{item}'" for item in sorted(duplicates))
            raise ValueError(
                f"Duplicate element ID(s) in diagram '{self.definitions_id}': {listed}. "
                "Every process, flow node, sequence flow, pool and lane must carry an ID "
                "that is unique across the whole diagram."
            )
        return self

    def _element_ids(self) -> list[str]:
        ids: list[str] = []
        for proc in self.processes:
            ids.append(proc.id)
            ids.extend(node.id for node in proc.flow_nodes)
            ids.extend(flow.id for flow in proc.sequence_flows)
            for pool in proc.pools:
                ids.append(pool.id)
                ids.extend(lane.id for lane in pool.lanes)
        return ids
