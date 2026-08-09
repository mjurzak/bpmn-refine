"""Versioned records emitted by the evaluation dataset generator."""

from __future__ import annotations

from enum import StrEnum

from app.repair.ops import AtomicEditOp
from pydantic import BaseModel, Field


class DefectClass(StrEnum):
    STRUCT = "STRUCT"
    SOUND = "SOUND"
    SEM = "SEM"


class OperatorId(StrEnum):
    DELETE_ONLY_START = "S01"
    DELETE_ONLY_END = "S02"
    XOR_SPLIT_AND_JOIN = "F01"
    AND_SPLIT_XOR_JOIN = "F02"
    DELETE_PARALLEL_JOIN = "F03"
    DELETE_BRIDGE_FLOW = "F04"


class GroundTruthRecord(BaseModel):
    """Expected result for one generated variant."""

    variant_id: str
    seed: str
    operators: list[OperatorId]
    defect_class: DefectClass = Field(alias="class")
    expected_finding: str
    expected_elements: list[str]
    injection_site: list[str]
    injection: list[AtomicEditOp] = Field(default_factory=list)
    repair: list[AtomicEditOp]
    detected_by_construction: bool
    detected_by: dict[str, list[str]] = Field(default_factory=dict)
    anchor: None = None

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
    operators: list[OperatorId]
    counts: dict[str, int]
    files: list[FileRecord]
