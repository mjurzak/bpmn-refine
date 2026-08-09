"""Read-only access to generated evaluation datasets for visual inspection."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/evaluation/datasets", tags=["evaluation"])

DATASET_ROOT = Path(__file__).resolve().parents[4] / "data" / "eval"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._+-]+$")


class VariantSummary(BaseModel):
    id: str
    operators: list[str]
    defect_class: str
    expected_finding: str
    injection_site: list[str]


class SeedSummary(BaseModel):
    id: str
    variants: list[VariantSummary]


class DatasetIndex(BaseModel):
    version: str
    seeds: list[SeedSummary]


class DatasetComparison(BaseModel):
    version: str
    seed: str
    variant: VariantSummary
    original_xml: str
    variant_xml: str


@router.get("/{version}", response_model=DatasetIndex)
def dataset_index(version: str) -> DatasetIndex:
    """List seeds and their matching variants for one dataset snapshot."""
    dataset = _dataset_dir(version)
    grouped: dict[str, list[VariantSummary]] = {}
    for truth_path in sorted((dataset / "ground_truth").glob("*.json")):
        truth = _read_truth(truth_path)
        variant_id = truth_path.stem
        seed_id = str(truth.get("seed", ""))
        if not seed_id or truth.get("variant_id") != variant_id:
            continue
        if not (dataset / "seeds" / f"{seed_id}.bpmn").is_file():
            continue
        if not (dataset / "variants" / f"{variant_id}.bpmn").is_file():
            continue
        grouped.setdefault(seed_id, []).append(_variant_summary(variant_id, truth))

    return DatasetIndex(
        version=version,
        seeds=[
            SeedSummary(id=seed_id, variants=variants)
            for seed_id, variants in sorted(grouped.items())
        ],
    )


@router.get("/{version}/comparisons/{variant_id}", response_model=DatasetComparison)
def dataset_comparison(version: str, variant_id: str) -> DatasetComparison:
    """Return one original/variant XML pair and its ground-truth summary."""
    dataset = _dataset_dir(version)
    _require_safe(variant_id, "variant")
    truth_path = dataset / "ground_truth" / f"{variant_id}.json"
    if not truth_path.is_file():
        raise HTTPException(status_code=404, detail="Dataset variant not found")

    truth = _read_truth(truth_path)
    if truth.get("variant_id") != variant_id:
        raise HTTPException(status_code=409, detail="Ground-truth stem does not match variant id")
    seed_id = str(truth.get("seed", ""))
    _require_safe(seed_id, "seed")
    original_path = dataset / "seeds" / f"{seed_id}.bpmn"
    variant_path = dataset / "variants" / f"{variant_id}.bpmn"
    if not original_path.is_file() or not variant_path.is_file():
        raise HTTPException(status_code=409, detail="Dataset comparison files are incomplete")

    return DatasetComparison(
        version=version,
        seed=seed_id,
        variant=_variant_summary(variant_id, truth),
        original_xml=original_path.read_text(encoding="utf-8"),
        variant_xml=variant_path.read_text(encoding="utf-8"),
    )


def _dataset_dir(version: str) -> Path:
    _require_safe(version, "dataset version")
    dataset = DATASET_ROOT / version
    if not dataset.is_dir():
        raise HTTPException(status_code=404, detail="Dataset version not found")
    return dataset


def _require_safe(value: str, label: str) -> None:
    if not value or not _SAFE_NAME.fullmatch(value):
        raise HTTPException(status_code=404, detail=f"Invalid {label}")


def _read_truth(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Invalid ground truth: {path.name}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail=f"Invalid ground truth: {path.name}")
    return payload


def _variant_summary(variant_id: str, truth: dict) -> VariantSummary:
    return VariantSummary(
        id=variant_id,
        operators=[str(item) for item in truth.get("operators", [])],
        defect_class=str(truth.get("class", "")),
        expected_finding=str(truth.get("expected_finding", "")),
        injection_site=[str(item) for item in truth.get("injection_site", [])],
    )
