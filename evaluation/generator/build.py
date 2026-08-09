"""Emit the deterministic, non-LLM portion of an evaluation dataset."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from app.model.registry import get_converter
from app.model.schema import BpmnDiagram
from app.repair.ops import apply_edit_ops
from app.validation.checkers import run_woflan
from app.validation.rules import validate
from evaluation.generator.models import (
    DatasetManifest,
    DefectClass,
    FileRecord,
    GroundTruthRecord,
    OperatorId,
)
from evaluation.generator.exclusions import render_exclusions, render_seed_list
from evaluation.generator.operators import (
    soundness_candidate_injections,
    soundness_injections,
    structural_injections,
)
from evaluation.generator.probe import ProbeReport

GENERATOR_VERSION = "1.3"
DEFAULT_RANDOM_SEED = 20260809
_GENERATED_PATHS = (
    "manifest.json",
    "ATTRIBUTION.md",
    "applicability.json",
    "EXCLUSIONS.md",
    "probe.json",
    "seeds.txt",
    "seeds",
    "variants",
    "ground_truth",
    "descriptions",
)


def census_soundness_sites(
    *,
    source_dir: Path,
    probe_path: Path,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> dict:
    """Report eligible and selected F01/F02 sites without writing variants."""
    report = ProbeReport.model_validate_json(probe_path.read_bytes())
    converter = get_converter()
    records = []
    soundness_operators = [
        OperatorId.XOR_SPLIT_AND_JOIN,
        OperatorId.AND_SPLIT_XOR_JOIN,
        OperatorId.DELETE_PARALLEL_JOIN,
        OperatorId.DELETE_BRIDGE_FLOW,
    ]
    counts = {
        f"{kind}_{operator.value}": 0
        for kind in ("eligible", "selected")
        for operator in soundness_operators
    }
    for record in report.seeds:
        diagram = converter.parse(
            (source_dir / f"{record.seed}.bpmn").read_bytes()
        )
        eligible = soundness_candidate_injections(diagram)
        selected = soundness_injections(
            diagram,
            seed_id=record.seed,
            random_seed=random_seed,
        )
        for injection in eligible:
            counts[f"eligible_{injection.operator.value}"] += 1
        for injection in selected:
            counts[f"selected_{injection.operator.value}"] += 1
        records.append(
            {
                "seed": record.seed,
                "eligible": [
                    {
                        "operator": injection.operator.value,
                        "site": list(injection.site),
                    }
                    for injection in eligible
                ],
                "selected": [
                    {
                        "operator": injection.operator.value,
                        "site": list(injection.site),
                    }
                    for injection in selected
                ],
            }
        )
    return {
        "generator_version": GENERATOR_VERSION,
        "random_seed": random_seed,
        "counts": counts,
        "records": records,
    }


def build_dataset(
    *,
    source_dir: Path,
    description_dir: Path,
    probe_path: Path,
    out: Path,
    dataset_version: str,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> DatasetManifest:
    """Snapshot eligible seeds and emit deterministic S01/S02/F01/F02 variants.

    The function refuses to overwrite generated content. Dataset versions become
    immutable once experiments cite them, so replacement must be an explicit
    filesystem operation outside the generator.
    """
    _require_empty_targets(out)
    probe_bytes = probe_path.read_bytes()
    report = ProbeReport.model_validate_json(probe_bytes)

    applicability = census_soundness_sites(
        source_dir=source_dir,
        probe_path=probe_path,
        random_seed=random_seed,
    )

    seed_dir = out / "seeds"
    variant_dir = out / "variants"
    truth_dir = out / "ground_truth"
    seed_description_dir = out / "descriptions" / "seeds"
    variant_description_dir = out / "descriptions" / "variants"
    for directory in (
        seed_dir,
        variant_dir,
        truth_dir,
        seed_description_dir,
        variant_description_dir,
    ):
        directory.mkdir(parents=True, exist_ok=False)

    converter = get_converter()
    files: list[FileRecord] = []
    variant_count = 0
    operator_counts = {operator.value: 0 for operator in OperatorId}

    for record in report.seeds:
        source = source_dir / f"{record.seed}.bpmn"
        description = description_dir / f"{record.seed}.txt"
        source_bytes = source.read_bytes()
        source_hash = _sha256(source_bytes)
        if not record.source_hash or not source_hash.startswith(record.source_hash):
            raise ValueError(
                f"source hash changed for seed {record.seed}; rerun the probe"
            )
        if not description.is_file():
            raise FileNotFoundError(f"description not found: {description}")

        diagram, unsupported = converter.parse_with_diagnostics(source_bytes)
        if unsupported:
            tags = ", ".join(sorted({item.tag for item in unsupported}))
            raise ValueError(
                f"seed {record.seed} is no longer lossless ({tags}); rerun the probe"
            )
        if validate(diagram).issues:
            raise ValueError(
                f"seed {record.seed} now has tier-1 findings; rerun the probe"
            )

        seed_target = seed_dir / source.name
        description_target = seed_description_dir / description.name
        shutil.copyfile(source, seed_target)
        shutil.copyfile(description, description_target)
        files.extend(
            [
                _file_record(out, seed_target),
                _file_record(out, description_target),
            ]
        )

        injections = structural_injections(diagram) + soundness_injections(
            diagram,
            seed_id=record.seed,
            random_seed=random_seed,
        )
        for injection in injections:
            variant_id = _variant_id(record.seed, injection.operator, injection.site)
            variant_path = variant_dir / f"{variant_id}.bpmn"
            truth_path = truth_dir / f"{variant_id}.json"
            variant_description = variant_description_dir / f"{variant_id}.txt"

            variant_bytes = converter.serialize(injection.diagram)
            reparsed, dropped = converter.parse_with_diagnostics(variant_bytes)
            if dropped:
                raise ValueError(f"generated variant {variant_id} is lossy")
            findings = {issue.rule_id for issue in validate(reparsed).issues}
            if (
                injection.defect_class is DefectClass.STRUCT
                and findings != {injection.expected_finding}
            ):
                raise ValueError(
                    f"variant {variant_id} produced findings {sorted(findings)}"
                )
            if (
                injection.operator is OperatorId.DELETE_BRIDGE_FLOW
                and (
                    injection.expected_finding not in findings
                    or findings - {"R007", "R008"}
                )
            ):
                raise ValueError(
                    f"bridge-flow variant {variant_id} produced findings "
                    f"{sorted(findings)}"
                )
            if (
                injection.defect_class is DefectClass.SOUND
                and injection.operator is not OperatorId.DELETE_BRIDGE_FLOW
                and findings
            ):
                raise ValueError(
                    f"soundness variant {variant_id} produced tier-1 findings "
                    f"{sorted(findings)}"
                )

            woflan_findings = (
                []
                if injection.defect_class is DefectClass.STRUCT
                else [issue.rule_id for issue in run_woflan(reparsed)]
            )
            detected_by = {
                "tier1": sorted(findings),
                "woflan": sorted(woflan_findings),
            }
            detected = any(detected_by.values())

            variant_path.write_bytes(variant_bytes)
            shutil.copyfile(description, variant_description)
            truth = GroundTruthRecord(
                variant_id=variant_id,
                seed=record.seed,
                operators=[injection.operator],
                **{"class": injection.defect_class},
                expected_finding=injection.expected_finding,
                expected_elements=injection.expected_elements,
                injection_site=list(injection.site),
                injection=injection.injection,
                repair=injection.repair,
                detected_by_construction=detected,
                detected_by=detected_by,
            )
            truth_path.write_text(
                json.dumps(
                    truth.model_dump(mode="json", by_alias=True),
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            files.extend(
                [
                    _file_record(out, variant_path),
                    _file_record(out, truth_path),
                    _file_record(out, variant_description),
                ]
            )
            variant_count += 1
            operator_counts[injection.operator.value] += 1

    metadata = {
        "probe.json": probe_bytes,
        "EXCLUSIONS.md": render_exclusions(report).encode(),
        "seeds.txt": render_seed_list(report).encode(),
        "applicability.json": (
            json.dumps(applicability, indent=2, ensure_ascii=False) + "\n"
        ).encode(),
    }
    for name, payload in metadata.items():
        path = out / name
        path.write_bytes(payload)
        files.append(_file_record(out, path))

    attribution = out / "ATTRIBUTION.md"
    attribution.write_text(_attribution_text(), encoding="utf-8")
    files.append(_file_record(out, attribution))

    manifest = DatasetManifest(
        dataset_version=dataset_version,
        generator_version=GENERATOR_VERSION,
        random_seed=random_seed,
        source={
            "name": "PMo Dataset",
            "version": "1.0.0",
            "doi": "10.5281/zenodo.15857589",
            "license": "CC-BY-4.0",
        },
        probe={
            "version": report.probe_version,
            "sha256": _sha256(probe_bytes),
        },
        operators=list(OperatorId),
        counts={
            "seeds": len(report.seeds),
            "variants": variant_count,
            **operator_counts,
        },
        files=sorted(files, key=lambda item: item.path),
    )
    (out / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def audit_dataset(out: Path) -> dict[str, int]:
    """Verify hashes, stem joins, expected findings, and inverse repairs."""
    manifest = DatasetManifest.model_validate_json(
        (out / "manifest.json").read_bytes()
    )
    for record in manifest.files:
        path = out / record.path
        if not path.is_file():
            raise ValueError(f"manifest file is missing: {record.path}")
        if _sha256(path.read_bytes()) != record.sha256:
            raise ValueError(f"hash mismatch: {record.path}")

    variants = {path.stem: path for path in (out / "variants").glob("*.bpmn")}
    truths = {path.stem: path for path in (out / "ground_truth").glob("*.json")}
    descriptions = {
        path.stem: path
        for path in (out / "descriptions" / "variants").glob("*.txt")
    }
    if variants.keys() != truths.keys() or variants.keys() != descriptions.keys():
        raise ValueError("variant, ground-truth, and description stems do not match")

    converter = get_converter()
    for variant_id in sorted(variants):
        truth = GroundTruthRecord.model_validate_json(truths[variant_id].read_bytes())
        if truth.variant_id != variant_id:
            raise ValueError(f"ground-truth id mismatch: {variant_id}")
        variant, unsupported = converter.parse_with_diagnostics(
            variants[variant_id].read_bytes()
        )
        if unsupported:
            raise ValueError(f"variant is lossy: {variant_id}")
        findings = {issue.rule_id for issue in validate(variant).issues}
        if (
            truth.defect_class is DefectClass.STRUCT
            and findings != {truth.expected_finding}
        ):
            raise ValueError(
                f"unexpected findings for {variant_id}: {sorted(findings)}"
            )
        if (
            OperatorId.DELETE_BRIDGE_FLOW in truth.operators
            and (
                truth.expected_finding not in findings
                or findings - {"R007", "R008"}
            )
        ):
            raise ValueError(
                f"unexpected bridge-flow findings for {variant_id}: "
                f"{sorted(findings)}"
            )
        if (
            truth.defect_class is DefectClass.SOUND
            and OperatorId.DELETE_BRIDGE_FLOW not in truth.operators
            and findings
        ):
            raise ValueError(
                f"soundness variant has tier-1 findings: {variant_id}"
            )
        seed = converter.parse((out / "seeds" / f"{truth.seed}.bpmn").read_bytes())
        if truth.injection:
            injected, injection_results = apply_edit_ops(truth.injection, seed)
            if not all(result.applied for result in injection_results):
                raise ValueError(f"stored injection failed: {variant_id}")
            if _without_layout(injected) != _without_layout(variant):
                raise ValueError(
                    f"stored injection does not reconstruct variant: {variant_id}"
                )
        repaired, results = apply_edit_ops(truth.repair, variant)
        if not all(result.applied for result in results):
            raise ValueError(f"stored repair failed: {variant_id}")
        if _without_layout(repaired) != _without_layout(seed):
            raise ValueError(f"stored repair does not restore seed semantics: {variant_id}")

    if len(variants) != manifest.counts.get("variants"):
        raise ValueError("manifest variant count does not match files")
    return {"seeds": manifest.counts["seeds"], "variants": len(variants)}


def _require_empty_targets(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    occupied = [name for name in _GENERATED_PATHS if (out / name).exists()]
    if occupied:
        raise FileExistsError(
            "refusing to overwrite generated dataset content: "
            + ", ".join(occupied)
        )


def _variant_id(seed: str, operator: OperatorId, site: tuple[str, ...]) -> str:
    safe_site = "__".join(
        re.sub(r"[^A-Za-z0-9_-]+", "_", item).strip("_") for item in site
    )
    return f"{seed}_{operator.value}_{safe_site}"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_record(root: Path, path: Path) -> FileRecord:
    return FileRecord(path=path.relative_to(root).as_posix(), sha256=_sha256(path.read_bytes()))


def _without_layout(diagram: BpmnDiagram) -> BpmnDiagram:
    stripped = diagram.model_copy(deep=True)
    # BPMNDI serialization may add namespace declarations to a source that had
    # no layout. Namespace spelling has no control-flow semantics.
    stripped.namespaces = {}
    for process in stripped.processes:
        for node in process.flow_nodes:
            node.bounds = None
            node.label_bounds = None
            node.incoming.sort()
            node.outgoing.sort()
        for flow in process.sequence_flows:
            flow.waypoints = []
            flow.label_bounds = None
        process.flow_nodes.sort(key=lambda node: node.id)
        process.sequence_flows.sort(key=lambda flow: flow.id)
    stripped.processes.sort(key=lambda process: process.id)
    return stripped


def _attribution_text() -> str:
    return """# Attribution

This dataset contains modified BPMN models and copied process descriptions from
the PMo Dataset v1.0.0 by Alexis Brissard, Frédéric Cuppens, and Amal Zouaq:
https://doi.org/10.5281/zenodo.15857589.

The source dataset is licensed under Creative Commons Attribution 4.0
(CC BY 4.0). The files in `variants/` modify the source BPMN models by applying
the defect operators recorded in the matching `ground_truth/` files. The files
in `seeds/` and `descriptions/seeds/` are unmodified copies. Variant descriptions
are unmodified copies associated with the corresponding source model.

Generator code, operator identifiers, file hashes, and generation parameters are
recorded in `manifest.json` and `evaluation/generator/`.
"""
