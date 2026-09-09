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
    multiplicity: int = 1
    interaction: str = "single"


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


class EnhancementSummary(BaseModel):
    id: str
    seed_id: str
    operator: str
    instruction: str
    relation_type: str
    expected_element_ids: list[str]


class EnhancementIndex(BaseModel):
    cases: list[EnhancementSummary]


class EnhancementComparison(BaseModel):
    case: EnhancementSummary
    core_xml: str
    reference_xml: str
    relation: dict
    d_core: str
    d_extra: str


@router.get("/enhancement", response_model=EnhancementIndex)
async def enhancement_index() -> EnhancementIndex:
    """List deterministic enhancement cases for visual inspection."""
    return EnhancementIndex(
        cases=[_enhancement_summary(case) for case in _read_enhancement_cases()]
    )


@router.get(
    "/enhancement/comparisons/{case_id}",
    response_model=EnhancementComparison,
)
async def enhancement_comparison(case_id: str) -> EnhancementComparison:
    """Return the core/reference pair and semantic contract for one case."""
    _require_safe(case_id, "enhancement case")
    cases = _read_enhancement_cases()
    case = next((item for item in cases if item.get("case_id") == case_id), None)
    if case is None:
        raise HTTPException(status_code=404, detail="Enhancement case not found")

    root = DATASET_ROOT / "enhancement"
    core_path = _enhancement_artifact(root, case.get("core"))
    reference_path = _enhancement_artifact(root, case.get("reference"))
    return EnhancementComparison(
        case=_enhancement_summary(case),
        core_xml=core_path.read_text(encoding="utf-8"),
        reference_xml=reference_path.read_text(encoding="utf-8"),
        relation=case.get("relation") if isinstance(case.get("relation"), dict) else {},
        d_core=str(case.get("d_core", "")),
        d_extra=str(case.get("d_extra", "")),
    )


@router.get("/{version}", response_model=DatasetIndex)
async def dataset_index(version: str) -> DatasetIndex:
    """List seeds and their matching variants for one dataset snapshot."""
    dataset = _dataset_dir(version)
    grouped: dict[str, list[VariantSummary]] = {}
    truth_root = dataset / "ground_truth"
    for truth_path in sorted(truth_root.rglob("*.json")):
        truth = _read_truth(truth_path)
        relative = truth_path.relative_to(truth_root).with_suffix("")
        variant_id = relative.as_posix()
        seed_id = str(truth.get("seed", ""))
        if not seed_id or truth.get("variant_id") != variant_id:
            continue
        if not (dataset / "seeds" / f"{seed_id}.bpmn").is_file():
            continue
        if not (dataset / "variants" / relative.with_suffix(".bpmn")).is_file():
            continue
        grouped.setdefault(seed_id, []).append(_variant_summary(variant_id, truth))

    return DatasetIndex(
        version=version,
        seeds=[
            SeedSummary(id=seed_id, variants=variants)
            for seed_id, variants in sorted(grouped.items())
        ],
    )


@router.get("/{version}/comparisons/{variant_id:path}", response_model=DatasetComparison)
async def dataset_comparison(version: str, variant_id: str) -> DatasetComparison:
    """Return one original/variant XML pair and its ground-truth summary."""
    dataset = _dataset_dir(version)
    relative = _require_safe_relative(variant_id, "variant")
    truth_path = dataset / "ground_truth" / relative.with_suffix(".json")
    if not truth_path.is_file():
        raise HTTPException(status_code=404, detail="Dataset variant not found")

    truth = _read_truth(truth_path)
    if truth.get("variant_id") != variant_id:
        raise HTTPException(status_code=409, detail="Ground-truth stem does not match variant id")
    seed_id = str(truth.get("seed", ""))
    _require_safe(seed_id, "seed")
    original_path = dataset / "seeds" / f"{seed_id}.bpmn"
    variant_path = dataset / "variants" / relative.with_suffix(".bpmn")
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


def _require_safe_relative(value: str, label: str) -> Path:
    parts = value.split("/")
    if not parts or any(
        part in {"", ".", ".."} or not _SAFE_NAME.fullmatch(part)
        for part in parts
    ):
        raise HTTPException(status_code=404, detail=f"Invalid {label}")
    return Path(*parts)


def _read_truth(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Invalid ground truth: {path.name}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail=f"Invalid ground truth: {path.name}")
    return payload


def _read_enhancement_cases() -> list[dict]:
    path = DATASET_ROOT / "enhancement" / "cases.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Enhancement dataset not found")
    payload = _read_truth(path)
    cases = payload.get("cases")
    if not isinstance(cases, list) or any(not isinstance(item, dict) for item in cases):
        raise HTTPException(status_code=500, detail="Invalid enhancement case index")
    return cases


def _enhancement_summary(case: dict) -> EnhancementSummary:
    metadata = case.get("metadata") if isinstance(case.get("metadata"), dict) else {}
    return EnhancementSummary(
        id=str(case.get("case_id", "")),
        seed_id=str(case.get("seed_id", "")),
        operator=str(metadata.get("operator", "")),
        instruction=str(case.get("instruction", "")),
        relation_type=str(
            case.get("relation", {}).get("type", "")
            if isinstance(case.get("relation"), dict)
            else ""
        ),
        expected_element_ids=[str(item) for item in case.get("expected_element_ids", [])],
    )


def _enhancement_artifact(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=409, detail="Enhancement case is incomplete")
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise HTTPException(status_code=409, detail="Enhancement artifact is invalid")
    return path


def _variant_summary(variant_id: str, truth: dict) -> VariantSummary:
    return VariantSummary(
        id=variant_id,
        operators=[str(item) for item in truth.get("operators", [])],
        defect_class=str(truth.get("class", "")),
        expected_finding=str(truth.get("expected_finding", "")),
        injection_site=[str(item) for item in truth.get("injection_site", [])],
        multiplicity=int(truth.get("multiplicity", 1)),
        interaction=str(truth.get("interaction", "single")),
    )
