"""Versioned records emitted by the evaluation dataset generator."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from app.repair.ops import AtomicEditOp
from pydantic import BaseModel, Field


class DefectClass(StrEnum):
    STRUCT = "STRUCT"
    SOUND = "SOUND"
    SEM = "SEM"


class InteractionRegime(StrEnum):
    SINGLE = "single"
    DISJOINT = "disjoint"
    INTERACTING = "interacting"


class OperatorId(StrEnum):
    DELETE_ONLY_START = "S01"
    DELETE_ONLY_END = "S02"
    DANGLING_FLOW_REF = "S03"
    XOR_SPLIT_AND_JOIN = "F01"
    AND_SPLIT_XOR_JOIN = "F02"
    DELETE_PARALLEL_JOIN = "F03"
    DELETE_BRIDGE_FLOW = "F04"


class RepointFlowOp(BaseModel):
    """Dataset-only mutation that can deliberately create a dangling reference."""

    op: Literal["repoint_flow"] = "repoint_flow"
    flow_id: str
    endpoint: Literal["source_ref", "target_ref"]
    new_ref: str


DatasetInjectionOp = AtomicEditOp | RepointFlowOp


class DefectExpectation(BaseModel):
    operator: str
    expected_finding: str
    expected_elements: list[str]
    injection_site: list[str]
    anchor: dict | None = None


class GroundTruthRecord(BaseModel):
    """Expected result for one generated variant."""

    variant_id: str
    seed: str
    operators: list[str]
    defect_class: DefectClass = Field(alias="class")
    expected_finding: str
    expected_findings: list[str] = Field(default_factory=list)
    expected_elements: list[str]
    injection_site: list[str]
    defects: list[DefectExpectation] = Field(default_factory=list)
    multiplicity: int = 1
    interaction: InteractionRegime = InteractionRegime.SINGLE
    injection: list[DatasetInjectionOp] = Field(default_factory=list)
    repair: list[AtomicEditOp]
    detected_by_construction: bool
    detected_by: dict[str, list[str]] = Field(default_factory=dict)
    anchor: dict | list[dict] | None = None
    human_verified: bool = False
    technical_verification: dict[str, bool] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class FileRecord(BaseModel):
    path: str
    sha256: str


class DatasetManifest(BaseModel):
    dataset_version: str
    generator_version: str
    random_seed: int
    source: dict[str, str]
    probe: dict[str, str]
    semantic_construction: dict | None = None
    operators: list[str]
    counts: dict[str, int]
    files: list[FileRecord]
