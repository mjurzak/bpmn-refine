"""Pydantic data models for the BPMN diagram domain.

Canonical in-memory representation. Everything needed to reconstruct valid BPMN
XML must live here, so the round-trip property (XML -> model -> XML) holds.
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


# plain strings at runtime, aliased only for readability
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


class EventDefinitionType(StrEnum):
    MESSAGE = "messageEventDefinition"
    TIMER = "timerEventDefinition"
    CONDITIONAL = "conditionalEventDefinition"
    SIGNAL = "signalEventDefinition"
    ERROR = "errorEventDefinition"
    ESCALATION = "escalationEventDefinition"
    COMPENSATE = "compensateEventDefinition"
    CANCEL = "cancelEventDefinition"
    TERMINATE = "terminateEventDefinition"
    LINK = "linkEventDefinition"


class EventDefinition(BaseModel):
    """The event trigger kind and its attributes.

    Nested trigger details such as timer schedules are deliberately outside the
    current IR and are reported by the import diagnostic instead of being lost.
    """

    type: EventDefinitionType
    id: str | None = None
    extra: dict = Field(default_factory=dict)


class FlowNode(BaseModel):
    id: FlowNodeId
    type: FlowNodeType
    name: str | None = None
    # sequence flow IDs, derived from the flows
    outgoing: list[SequenceFlowId] = Field(default_factory=list)
    incoming: list[SequenceFlowId] = Field(default_factory=list)
    event_definitions: list[EventDefinition] = Field(default_factory=list)
    # None means the node never had a shape, so the serialiser lays it out
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
    # empty means no edge shape, not a straight line
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
        """Enforce document-scoped ID uniqueness — BPMN 2.0 types `id` as xsd:ID."""
        duplicates = [
            element_id
            for element_id, count in Counter(self.element_ids()).items()
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

    @model_validator(mode="after")
    def _rebuild_adjacency(self) -> Self:
        """Derive every node's `incoming`/`outgoing` from the sequence flows.

        BPMN states each connection twice and the two can disagree; the flow wins.
        A no-op on an already-consistent diagram.
        """
        for proc in self.processes:
            node_index = {node.id: node for node in proc.flow_nodes}
            for node in proc.flow_nodes:
                node.incoming.clear()
                node.outgoing.clear()
            for flow in proc.sequence_flows:
                # a dangling endpoint wires nothing; R005/R006 report it instead
                source = node_index.get(flow.source_ref)
                if source is not None and flow.id not in source.outgoing:
                    source.outgoing.append(flow.id)
                target = node_index.get(flow.target_ref)
                if target is not None and flow.id not in target.incoming:
                    target.incoming.append(flow.id)
        return self

    def element_ids(self) -> list[str]:
        """every ID the document declares, in the order BPMN scopes them"""
        ids: list[str] = []
        for proc in self.processes:
            ids.append(proc.id)
            ids.extend(node.id for node in proc.flow_nodes)
            ids.extend(
                definition.id
                for node in proc.flow_nodes
                for definition in node.event_definitions
                if definition.id
            )
            ids.extend(flow.id for flow in proc.sequence_flows)
            for pool in proc.pools:
                ids.append(pool.id)
                ids.extend(lane.id for lane in pool.lanes)
        return ids
