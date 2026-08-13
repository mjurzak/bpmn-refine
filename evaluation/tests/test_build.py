"""Structural dataset generation is deterministic and self-checking."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.experiments import hash_bytes
from app.model.registry import get_converter
from app.repair.ops import apply_edit_ops
from app.validation.rules import validate
from evaluation.generator.build import (
    audit_dataset,
    build_dataset,
    refresh_dataset_manifest,
)
from evaluation.generator.models import GroundTruthRecord
from evaluation.generator.probe import ProbeRecord, ProbeReport, Verdict

from evaluation.tests.test_probe import SOUND


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    descriptions = tmp_path / "descriptions"
    source.mkdir()
    descriptions.mkdir()
    (source / "01.bpmn").write_bytes(SOUND)
    (descriptions / "01.txt").write_text(
        "The process starts, performs the work, and ends.\n",
        encoding="utf-8",
    )
    report = ProbeReport(
        source_dir=str(source),
        records=[
            ProbeRecord(
                seed="01",
                source_path=str(source / "01.bpmn"),
                source_hash=hash_bytes(SOUND),
                verdict=Verdict.ELIGIBLE,
                has_description=True,
            )
        ],
    )
    probe = tmp_path / "probe.json"
    probe.write_text(
        json.dumps(report.model_dump(mode="json")),
        encoding="utf-8",
    )
    return source, descriptions, probe


def test_build_emits_joinable_single_and_interacting_variants(tmp_path: Path):
    source, descriptions, probe = _fixture(tmp_path)
    out = tmp_path / "dataset"

    manifest = build_dataset(
        source_dir=source,
        description_dir=descriptions,
        probe_path=probe,
        out=out,
        dataset_version="test-v1",
    )

    variants = sorted((out / "variants").rglob("*.bpmn"))
    truths = sorted((out / "ground_truth").rglob("*.json"))
    assert manifest.counts == {
        "seeds": 1,
        "variants": 4,
        "S01": 2,
        "S02": 1,
        "S03": 2,
        "F01": 0,
        "F02": 0,
        "F03": 0,
        "F04": 0,
        "k1": 3,
        "k2_disjoint": 0,
        "k2_interacting": 1,
    }
    assert [
        path.relative_to(out / "variants").with_suffix("") for path in variants
    ] == [
        path.relative_to(out / "ground_truth").with_suffix("") for path in truths
    ]
    assert (out / "seeds" / "01.bpmn").read_bytes() == SOUND
    assert (out / "ATTRIBUTION.md").is_file()
    assert (out / "manifest.json").is_file()
    assert audit_dataset(out) == {"seeds": 1, "variants": 4}


@pytest.mark.parametrize("operator", ["S01", "S02", "S03"])
def test_stored_repairs_clear_the_injected_finding(tmp_path: Path, operator: str):
    source, descriptions, probe = _fixture(tmp_path)
    out = tmp_path / "dataset"
    build_dataset(
        source_dir=source,
        description_dir=descriptions,
        probe_path=probe,
        out=out,
        dataset_version="test-v1",
    )
    truth_path = next((out / "ground_truth").rglob(f"{operator}/*.json"))
    relative = truth_path.relative_to(out / "ground_truth").with_suffix(".bpmn")
    variant_path = out / "variants" / relative
    truth = GroundTruthRecord.model_validate_json(truth_path.read_bytes())
    diagram = get_converter().parse(variant_path.read_bytes())

    repaired, results = apply_edit_ops(truth.repair, diagram)

    assert all(result.applied for result in results)
    assert validate(repaired).issues == []
    assert {node.id for node in repaired.processes[0].flow_nodes} == {
        "Start_1",
        "Task_1",
        "End_1",
    }
    assert {flow.id for flow in repaired.processes[0].sequence_flows} == {
        "Flow_1",
        "Flow_2",
    }


def test_build_refuses_to_overwrite_a_dataset(tmp_path: Path):
    source, descriptions, probe = _fixture(tmp_path)
    out = tmp_path / "dataset"
    kwargs = {
        "source_dir": source,
        "description_dir": descriptions,
        "probe_path": probe,
        "out": out,
        "dataset_version": "test-v1",
    }
    build_dataset(**kwargs)

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        build_dataset(**kwargs)


def test_build_is_byte_deterministic(tmp_path: Path):
    source, descriptions, probe = _fixture(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    for out in (first, second):
        build_dataset(
            source_dir=source,
            description_dir=descriptions,
            probe_path=probe,
            out=out,
            dataset_version="test-v1",
        )

    first_files = {
        path.relative_to(first): path.read_bytes()
        for path in first.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second): path.read_bytes()
        for path in second.rglob("*")
        if path.is_file()
    }
    assert first_files == second_files


def test_audit_detects_a_modified_variant(tmp_path: Path):
    source, descriptions, probe = _fixture(tmp_path)
    out = tmp_path / "dataset"
    build_dataset(
        source_dir=source,
        description_dir=descriptions,
        probe_path=probe,
        out=out,
        dataset_version="test-v1",
    )
    variant = next((out / "variants").rglob("*.bpmn"))
    variant.write_bytes(variant.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="hash mismatch"):
        audit_dataset(out)


def test_refresh_manifest_tracks_an_explicit_curation_removal(tmp_path: Path):
    source, descriptions, probe = _fixture(tmp_path)
    out = tmp_path / "dataset"
    build_dataset(
        source_dir=source,
        description_dir=descriptions,
        probe_path=probe,
        out=out,
        dataset_version="test-v1",
    )
    relative = Path("single/S02/01")
    (out / "variants" / relative.with_suffix(".bpmn")).unlink()
    (out / "ground_truth" / relative.with_suffix(".json")).unlink()
    (out / "descriptions" / "variants" / relative.with_suffix(".txt")).unlink()

    manifest = refresh_dataset_manifest(out)

    assert manifest.counts["variants"] == 3
    assert manifest.counts["S02"] == 0
    assert manifest.counts["k1"] == 2
    assert audit_dataset(out) == {"seeds": 1, "variants": 3}
