from __future__ import annotations

import json
from pathlib import Path

from app.model.formats.pydantic_ir import PydanticConverter
from app.repair.ops import apply_edit_ops, edit_op_list_adapter
from evaluation.enhancement import (
    DEFAULT_OPERATOR_SEEDS,
    build_enhancement_dataset,
    load_m01_evidence,
)


DATASET = Path("data/eval/v1.0")


def test_m01_builder_emits_an_auditable_case(tmp_path: Path):
    out = tmp_path / "enhancement"
    manifest = build_enhancement_dataset(
        dataset_root=DATASET,
        out=out,
        seed_ids=("01",),
        verify_tier2=False,
    )

    assert manifest.cases == ["M01-refine-01"]
    payload = json.loads((out / "cases.json").read_text(encoding="utf-8"))
    case = payload["cases"][0]
    assert {
        "case_id",
        "seed_id",
        "core",
        "reference",
        "instruction",
        "oracle_plan",
        "removed_element_ids",
        "metadata",
    } <= case.keys()
    assert case["removed_element_ids"] == ["SequenceFlow_13", "Task_1"]
    assert case["metadata"]["operator"] == "M01"
    assert "manual_semantic_approval" not in case

    converter = PydanticConverter()
    reference, _ = converter.parse_with_diagnostics(
        (out / case["reference"]).read_bytes()
    )
    core, _ = converter.parse_with_diagnostics((out / case["core"]).read_bytes())
    plan = edit_op_list_adapter.validate_python(case["oracle_plan"])
    restored, results = apply_edit_ops(plan, core)
    assert all(result.applied for result in results)
    assert _semantic_projection(restored) == _semantic_projection(reference)


def test_m01_builder_is_byte_stable_and_idempotent(tmp_path: Path):
    out = tmp_path / "enhancement"
    build_enhancement_dataset(
        dataset_root=DATASET,
        out=out,
        seed_ids=("01", "20"),
        verify_tier2=False,
    )
    first = {
        path.relative_to(out).as_posix(): path.read_bytes()
        for path in out.rglob("*")
        if path.is_file()
    }

    build_enhancement_dataset(
        dataset_root=DATASET,
        out=out,
        seed_ids=("20", "01"),
        verify_tier2=False,
    )
    second = {
        path.relative_to(out).as_posix(): path.read_bytes()
        for path in out.rglob("*")
        if path.is_file()
    }
    assert first == second


def test_m01_evidence_is_taken_from_existing_verified_record():
    evidence = load_m01_evidence(DATASET, "35")
    assert evidence["source_ground_truth"] == "ground_truth/single/M01/35.json"
    assert evidence["verified_source_record"] is True
    assert evidence["expected_element_ids"] == ["ServiceTask_5"]


def test_default_builder_emits_three_cases_per_non_m01_family(tmp_path: Path):
    manifest = build_enhancement_dataset(
        dataset_root=DATASET,
        out=tmp_path / "enhancement",
        verify_tier2=False,
    )
    assert len(manifest.cases) == 21
    assert all(len(seeds) == 3 for op, seeds in DEFAULT_OPERATOR_SEEDS.items() if op != "M01")
    payload = json.loads((tmp_path / "enhancement" / "cases.json").read_text())
    cases = payload["cases"]
    assert {case["metadata"]["operator"] for case in cases} == set(DEFAULT_OPERATOR_SEEDS)
    assert all("manual_semantic_approval" not in case for case in cases)
    assert all(case["metadata"].get("source_human_verified") is True for case in cases)
    assert all(case["d_extra"] == case["instruction"] for case in cases)


def test_default_builder_is_byte_stable(tmp_path: Path):
    out = tmp_path / "enhancement"
    build_enhancement_dataset(dataset_root=DATASET, out=out, verify_tier2=False)
    first = {path.relative_to(out).as_posix(): path.read_bytes() for path in out.rglob("*") if path.is_file()}
    build_enhancement_dataset(dataset_root=DATASET, out=out, verify_tier2=False)
    second = {path.relative_to(out).as_posix(): path.read_bytes() for path in out.rglob("*") if path.is_file()}
    assert first == second


def _semantic_projection(diagram):
    return {
        "nodes": sorted(
            (node.id, node.type.value, node.name)
            for process in diagram.processes
            for node in process.flow_nodes
        ),
        "flows": sorted(
            (
                flow.id,
                flow.source_ref,
                flow.target_ref,
                flow.name,
                flow.condition_expression,
            )
            for process in diagram.processes
            for flow in process.sequence_flows
        ),
    }
