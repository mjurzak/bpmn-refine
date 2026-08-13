"""Emit the deterministic, non-LLM portion of an evaluation dataset."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import shutil
from pathlib import Path

from app.model.registry import get_converter
from app.model.schema import BpmnDiagram
from app.repair.ops import AtomicEditOp, apply_edit_ops
from app.validation.checkers import run_woflan
from app.validation.rules import validate
from evaluation.generator.models import (
    DatasetManifest,
    DatasetInjectionOp,
    DefectExpectation,
    DefectClass,
    FileRecord,
    GroundTruthRecord,
    InteractionRegime,
    OperatorId,
)
from evaluation.generator.exclusions import render_exclusions, render_seed_list
from evaluation.generator.operators import (
    CompositeInjection,
    Injection,
    apply_injection_ops,
    disjoint_injection,
    interacting_injection,
    soundness_candidate_injections,
    soundness_injections,
    structural_candidate_injections,
    structural_injections,
)
from evaluation.generator.probe import ProbeReport

GENERATOR_VERSION = "1.7"
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


@dataclass(frozen=True)
class _VariantPlan:
    defects: tuple[Injection, ...]
    defect_class: DefectClass
    diagram: BpmnDiagram
    injection: list[DatasetInjectionOp]
    repair: list[AtomicEditOp]
    interaction: InteractionRegime


def _single_plan(injection: Injection) -> _VariantPlan:
    return _VariantPlan(
        defects=(injection,),
        defect_class=injection.defect_class,
        diagram=injection.diagram,
        injection=injection.injection,
        repair=injection.repair,
        interaction=InteractionRegime.SINGLE,
    )


def _disjoint_plan(injection: CompositeInjection) -> _VariantPlan:
    return _VariantPlan(
        defects=injection.defects,
        defect_class=injection.defect_class,
        diagram=injection.diagram,
        injection=injection.injection,
        repair=injection.repair,
        interaction=InteractionRegime.DISJOINT,
    )


def _interacting_plan(injection: CompositeInjection) -> _VariantPlan:
    return _VariantPlan(
        defects=injection.defects,
        defect_class=injection.defect_class,
        diagram=injection.diagram,
        injection=injection.injection,
        repair=injection.repair,
        interaction=InteractionRegime.INTERACTING,
    )


def _validate_plan_findings(
    plan: _VariantPlan,
    issues: list,
    variant_id: str,
) -> None:
    findings = {issue.rule_id for issue in issues}
    rule_defects = [
        defect for defect in plan.defects
        if defect.expected_finding.startswith("R")
    ]
    for defect in rule_defects:
        if not _issue_matches(defect, issues):
            raise ValueError(
                f"variant {variant_id} did not produce "
                f"{defect.expected_finding} at {defect.expected_elements}: "
                f"{sorted(findings)}"
            )

    operators = {defect.operator for defect in plan.defects}
    if len(plan.defects) == 1 and plan.defect_class is DefectClass.STRUCT:
        if (
            OperatorId.DANGLING_FLOW_REF not in operators
            and findings != {plan.defects[0].expected_finding}
        ):
            raise ValueError(
                f"variant {variant_id} produced findings {sorted(findings)}"
            )
    if plan.defect_class is DefectClass.SOUND:
        if OperatorId.DELETE_BRIDGE_FLOW in operators:
            if findings - {"R007", "R008"}:
                raise ValueError(
                    f"bridge-flow variant {variant_id} produced findings "
                    f"{sorted(findings)}"
                )
        elif findings:
            raise ValueError(
                f"soundness variant {variant_id} produced tier-1 findings "
                f"{sorted(findings)}"
            )


def _issue_matches(defect: Injection, issues: list) -> bool:
    expected_elements = set(defect.expected_elements)
    return any(
        issue.rule_id == defect.expected_finding
        and (
            issue.element_id is None
            or not expected_elements
            or issue.element_id in expected_elements
        )
        for issue in issues
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
    """Snapshot eligible seeds and emit deterministic structural variants.

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
    multiplicity_counts = {"k1": 0, "k2_disjoint": 0, "k2_interacting": 0}

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

        single_injections = structural_injections(
            diagram,
            seed_id=record.seed,
            random_seed=random_seed,
        ) + soundness_injections(
            diagram,
            seed_id=record.seed,
            random_seed=random_seed,
        )
        candidates = structural_candidate_injections(
            diagram
        ) + soundness_candidate_injections(diagram)
        composite = disjoint_injection(
            diagram,
            candidates,
            seed_id=record.seed,
            random_seed=random_seed,
        )
        interacting = interacting_injection(
            diagram,
            candidates,
            seed_id=record.seed,
            random_seed=random_seed,
        )
        plans = [_single_plan(injection) for injection in single_injections]
        if composite is not None:
            plans.append(_disjoint_plan(composite))
        if interacting is not None:
            plans.append(_interacting_plan(interacting))

        for plan in plans:
            relative_base = _plan_relative_path(record.seed, plan)
            variant_id = relative_base.as_posix()
            variant_path = variant_dir / relative_base.with_suffix(".bpmn")
            truth_path = truth_dir / relative_base.with_suffix(".json")
            variant_description = (
                variant_description_dir / relative_base.with_suffix(".txt")
            )
            for path in (variant_path, truth_path, variant_description):
                path.parent.mkdir(parents=True, exist_ok=True)

            variant_bytes = converter.serialize(plan.diagram)
            reparsed, dropped = converter.parse_with_diagnostics(variant_bytes)
            if dropped:
                raise ValueError(f"generated variant {variant_id} is lossy")
            findings = {issue.rule_id for issue in validate(reparsed).issues}
            issues = validate(reparsed).issues
            _validate_plan_findings(plan, issues, variant_id)

            woflan_findings = (
                []
                if plan.defect_class is DefectClass.STRUCT
                else [issue.rule_id for issue in run_woflan(reparsed)]
            )
            detected_by = {
                "tier1": sorted(findings),
                "woflan": sorted(woflan_findings),
            }
            detected = any(detected_by.values())

            variant_path.write_bytes(variant_bytes)
            shutil.copyfile(description, variant_description)
            expectations = [
                DefectExpectation(
                    operator=defect.operator,
                    expected_finding=defect.expected_finding,
                    expected_elements=defect.expected_elements,
                    injection_site=list(defect.site),
                )
                for defect in plan.defects
            ]
            expected_findings = [
                defect.expected_finding for defect in plan.defects
            ]
            truth = GroundTruthRecord(
                variant_id=variant_id,
                seed=record.seed,
                operators=[defect.operator for defect in plan.defects],
                **{"class": plan.defect_class},
                expected_finding=" + ".join(expected_findings),
                expected_findings=expected_findings,
                expected_elements=list(dict.fromkeys(
                    element
                    for defect in plan.defects
                    for element in defect.expected_elements
                )),
                injection_site=[
                    element
                    for defect in plan.defects
                    for element in defect.site
                ],
                defects=expectations,
                multiplicity=len(plan.defects),
                interaction=plan.interaction,
                injection=plan.injection,
                repair=plan.repair,
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
            for defect in plan.defects:
                operator_counts[defect.operator.value] += 1
            if plan.interaction is InteractionRegime.SINGLE:
                multiplicity_counts["k1"] += 1
            elif plan.interaction is InteractionRegime.DISJOINT:
                multiplicity_counts["k2_disjoint"] += 1
            else:
                multiplicity_counts["k2_interacting"] += 1

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
            **multiplicity_counts,
        },
        files=sorted(files, key=lambda item: item.path),
    )
    (out / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def audit_dataset(out: Path) -> dict[str, int]:
    """Verify hashes, relative-path joins, findings, and inverse repairs."""
    manifest = DatasetManifest.model_validate_json(
        (out / "manifest.json").read_bytes()
    )
    for record in manifest.files:
        path = out / record.path
        if not path.is_file():
            raise ValueError(f"manifest file is missing: {record.path}")
        if _sha256(path.read_bytes()) != record.sha256:
            raise ValueError(f"hash mismatch: {record.path}")

    variants = _relative_artifacts(out / "variants", ".bpmn")
    truths = _relative_artifacts(out / "ground_truth", ".json")
    descriptions = _relative_artifacts(
        out / "descriptions" / "variants", ".txt"
    )
    if variants.keys() != truths.keys() or variants.keys() != descriptions.keys():
        raise ValueError(
            "variant, ground-truth, and description paths do not match"
        )

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
        issues = validate(variant).issues
        findings = {issue.rule_id for issue in issues}
        expectations = truth.defects or [
            DefectExpectation(
                operator=truth.operators[0],
                expected_finding=truth.expected_finding,
                expected_elements=truth.expected_elements,
                injection_site=truth.injection_site,
            )
        ]
        for expectation in expectations:
            if (
                expectation.expected_finding.startswith("R")
                and not _truth_issue_matches(expectation, issues)
            ):
                raise ValueError(
                    f"missing {expectation.expected_finding} for {variant_id}: "
                    f"{sorted(findings)}"
                )
        if (
            truth.multiplicity == 1
            and truth.defect_class is DefectClass.STRUCT
            and OperatorId.DANGLING_FLOW_REF not in truth.operators
            and findings != {truth.expected_finding}
        ):
            raise ValueError(
                f"unexpected findings for {variant_id}: {sorted(findings)}"
            )
        if (
            OperatorId.DELETE_BRIDGE_FLOW in truth.operators
            and findings - {"R007", "R008"}
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
            injected, injection_results = apply_injection_ops(truth.injection, seed)
            if not all(injection_results):
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


def refresh_dataset_manifest(out: Path) -> DatasetManifest:
    """Recompute counts and hashes after an explicitly reviewed curation edit."""
    manifest_path = out / "manifest.json"
    current = DatasetManifest.model_validate_json(manifest_path.read_bytes())
    truths = sorted((out / "ground_truth").rglob("*.json"))
    records = [GroundTruthRecord.model_validate_json(path.read_bytes()) for path in truths]
    operator_counts: dict[str, int] = {operator: 0 for operator in current.operators}
    multiplicity_counts = {"k1": 0, "k2_disjoint": 0, "k2_interacting": 0}
    for record in records:
        for operator in record.operators:
            operator_counts[operator] = operator_counts.get(operator, 0) + 1
        if record.multiplicity == 1:
            multiplicity_counts["k1"] += 1
        elif record.interaction is InteractionRegime.DISJOINT:
            multiplicity_counts["k2_disjoint"] += 1
        elif record.interaction is InteractionRegime.INTERACTING:
            multiplicity_counts["k2_interacting"] += 1

    files = sorted(
        (
            _file_record(out, path)
            for path in out.rglob("*")
            if path.is_file() and path != manifest_path
        ),
        key=lambda item: item.path,
    )
    refreshed = current.model_copy(
        update={
            "operators": sorted(operator_counts),
            "counts": {
                "seeds": len(list((out / "seeds").glob("*.bpmn"))),
                "variants": len(records),
                **dict(sorted(operator_counts.items())),
                **multiplicity_counts,
            },
            "files": files,
        }
    )
    manifest_path.write_text(
        json.dumps(refreshed.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return refreshed


def _truth_issue_matches(expectation: DefectExpectation, issues: list) -> bool:
    expected_elements = set(expectation.expected_elements)
    return any(
        issue.rule_id == expectation.expected_finding
        and (
            issue.element_id is None
            or not expected_elements
            or issue.element_id in expected_elements
        )
        for issue in issues
    )


def _require_empty_targets(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    occupied = [name for name in _GENERATED_PATHS if (out / name).exists()]
    if occupied:
        raise FileExistsError(
            "refusing to overwrite generated dataset content: "
            + ", ".join(occupied)
        )


def _plan_relative_path(seed: str, plan: _VariantPlan) -> Path:
    operators = "+".join(defect.operator.value for defect in plan.defects)
    return Path(plan.interaction.value) / operators / seed


def _relative_artifacts(root: Path, suffix: str) -> dict[str, Path]:
    return {
        path.relative_to(root).with_suffix("").as_posix(): path
        for path in root.rglob(f"*{suffix}")
    }


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
