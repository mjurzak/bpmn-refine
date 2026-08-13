"""Provider-free, deterministic builder for E8 enhancement cases.

The builder reverses human-verified semantic mutations from dataset v1.0.
Each oracle uses production ``AtomicEditOp`` operations and restores the clean
reference. M01 uses the verified missing-step evidence directly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.model.formats.pydantic_ir import PydanticConverter
from app.model.schema import BpmnDiagram, FlowNodeType
from app.repair.ops import (
    AddFlowOp,
    AddNodeOp,
    AtomicEditOp,
    RemoveFlowOp,
    RemoveNodeOp,
    apply_edit_ops,
    edit_op_list_adapter,
)
from app.validation.rules import ValidationTier, validate


BUILDER_VERSION = "enhancement-builder@1"
DEFAULT_M01_SEEDS = ("01", "20", "35", "43", "49", "52")
DEFAULT_OPERATOR_SEEDS: dict[str, tuple[str, ...]] = {
    "M01": DEFAULT_M01_SEEDS,
    "M02": ("02", "31", "46"),
    "M03": ("36", "43", "49"),
    "M04": ("09", "40", "48"),
    "M06": ("03", "31", "49"),
    "M07": ("13", "40", "49"),
}
_TASK_TYPES = {
    FlowNodeType.TASK,
    FlowNodeType.USER_TASK,
    FlowNodeType.SERVICE_TASK,
    FlowNodeType.SCRIPT_TASK,
    FlowNodeType.SEND_TASK,
    FlowNodeType.RECEIVE_TASK,
    FlowNodeType.MANUAL_TASK,
    FlowNodeType.CALL_ACTIVITY,
    FlowNodeType.SUB_PROCESS,
}


class SourceLine(BaseModel):
    line: int
    text: str


class EnhancementCase(BaseModel):
    """The JSON contract shared by construction, audit, and the runner."""

    schema_version: Literal["enhancement-case@1"] = "enhancement-case@1"
    case_id: str
    seed_id: str
    core: str
    reference: str
    instruction: str
    d_core: str
    d_extra: str
    source_lines: list[SourceLine]
    relation: dict[str, Any]
    expected_element_ids: list[str]
    removed_element_ids: list[str]
    restored_element_ids: list[str]
    preserved_element_ids: list[str]
    oracle_plan: list[AtomicEditOp]
    metadata: dict[str, Any] = Field(default_factory=dict)


class EnhancementManifest(BaseModel):
    schema_version: Literal["enhancement-manifest@1"] = "enhancement-manifest@1"
    builder_version: str = BUILDER_VERSION
    dataset_version: str
    source: dict[str, Any]
    construction: dict[str, Any]
    cases: list[str]
    files: list[dict[str, str]]


@dataclass(frozen=True)
class BuiltEnhancement:
    case: EnhancementCase
    core: BpmnDiagram
    reference: BpmnDiagram


def load_verified_evidence(
    dataset_root: Path, operator: str, seed_id: str
) -> dict[str, Any]:
    """Load one single-operator, human-verified source record."""

    path = dataset_root / "ground_truth" / "single" / operator / f"{seed_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"{operator} evidence not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("operators") != [operator] or not payload.get("human_verified"):
        raise ValueError(f"evidence is not a verified single {operator} record: {path}")
    anchor = payload.get("anchor")
    if not isinstance(anchor, Mapping):
        defects = payload.get("defects") or []
        anchor = defects[0].get("anchor") if defects else None
    if not isinstance(anchor, Mapping):
        raise ValueError(f"{operator} evidence has no anchor: {path}")
    lines = anchor.get("description_lines") or []
    relation = anchor.get("relation")
    if not lines or not isinstance(relation, Mapping):
        raise ValueError(f"{operator} evidence has no source anchor: {path}")
    repair = payload.get("repair")
    injection = payload.get("injection")
    expected = payload.get("expected_elements")
    if not isinstance(repair, list) or not isinstance(injection, list):
        raise ValueError(f"{operator} evidence has no replayable operations: {path}")
    if not isinstance(expected, list) or not expected:
        raise ValueError(f"{operator} evidence has no expected elements: {path}")
    return {
        "operator": operator,
        "type": "policy_or_prohibition_refinement" if operator == "M07" else "semantic_refinement",
        "expected_element_ids": [str(item) for item in expected],
        "source_lines": lines,
        "relation": dict(relation),
        "injection": injection,
        "repair": repair,
        "expected_finding": str(payload.get("expected_finding", "")),
        "source_ground_truth": path.relative_to(dataset_root).as_posix(),
        "verified_source_record": True,
    }


def build_verified_case(
    reference: BpmnDiagram,
    description: str,
    *,
    operator: str,
    case_id: str,
    seed_id: str,
    evidence: Mapping[str, Any],
    core_path: str,
    reference_path: str,
    source_metadata: Mapping[str, Any] | None = None,
    verify_tier2: bool = False,
) -> BuiltEnhancement:
    """Build a semantic case by replaying verified injection and repair ops."""

    converter = PydanticConverter()
    _require_clean(reference, "reference")
    source_lines = _source_lines(evidence)
    description_lines = description.splitlines()
    line_numbers = {item.line for item in source_lines}
    for source_line in source_lines:
        if source_line.line < 1 or source_line.line > len(description_lines):
            raise ValueError(f"source line is outside description: {source_line.line}")
        if description_lines[source_line.line - 1] != source_line.text:
            raise ValueError(f"source line evidence changed: {source_line.line}")
    instruction = "\n".join(item.text for item in source_lines)
    d_core = "\n".join(
        text for index, text in enumerate(description_lines, start=1)
        if index not in line_numbers
    )
    injection = edit_op_list_adapter.validate_python(evidence["injection"])
    repair = edit_op_list_adapter.validate_python(evidence["repair"])
    core, injection_results = apply_edit_ops(injection, reference)
    if not all(result.applied for result in injection_results):
        raise ValueError(f"{operator} injection did not apply: {seed_id}")
    _require_clean(core, "core")
    restored, repair_results = apply_edit_ops(repair, core)
    if not all(result.applied for result in repair_results):
        raise ValueError(f"{operator} repair did not apply: {seed_id}")
    if _normalise(restored) != _normalise(reference):
        raise ValueError(f"{operator} repair does not restore reference: {seed_id}")

    reference_ids = set(reference.element_ids())
    core_ids = set(core.element_ids())
    removed_ids = sorted(reference_ids - core_ids)
    added_ids = sorted(core_ids - reference_ids)
    changed_ids = sorted(
        element_id
        for element_id in reference_ids & core_ids
        if _element_projection(reference, element_id) != _element_projection(core, element_id)
    )
    measurable_ids = {
        element.id
        for process in reference.processes
        for element in (*process.flow_nodes, *process.sequence_flows)
    }
    preserved_ids = sorted((reference_ids & core_ids & measurable_ids) - set(changed_ids))
    verification = _verification(
        reference,
        core,
        restored,
        converter,
        verify_tier2=verify_tier2,
    )
    if not all(
        value is True
        for key, value in verification.items()
        if key not in {"tier2_reference", "tier2_core"}
    ):
        raise ValueError(f"{operator} construction gates failed: {seed_id}")
    if verify_tier2 and not (
        verification["tier2_reference"] is True and verification["tier2_core"] is True
    ):
        raise ValueError(f"{operator} core/reference are not sound: {seed_id}")

    metadata = {
        "operator": operator,
        "type": evidence.get(
            "type",
            "policy_or_prohibition_refinement" if operator == "M07" else "semantic_refinement",
        ),
        "expected_finding": evidence.get("expected_finding", "semantic_refinement"),
        "source_seed": seed_id,
        "relation_type": evidence["relation"].get("type"),
        "changed_element_ids": changed_ids,
        "added_element_ids": added_ids,
        "source_record": evidence["source_ground_truth"],
        "source_human_verified": evidence["verified_source_record"],
        "provenance": {
            "dataset": "data/eval/v1.0",
            "source_ground_truth": evidence["source_ground_truth"],
            "source_human_verified": evidence["verified_source_record"],
        },
        "verification_status": verification,
        **dict(source_metadata or {}),
    }
    case = EnhancementCase(
        case_id=case_id,
        seed_id=seed_id,
        core=core_path,
        reference=reference_path,
        instruction=instruction,
        d_core=d_core,
        d_extra=instruction,
        source_lines=source_lines,
        relation=dict(evidence["relation"]),
        expected_element_ids=[str(item) for item in evidence["expected_element_ids"]],
        removed_element_ids=removed_ids,
        restored_element_ids=sorted(reference_ids),
        preserved_element_ids=preserved_ids,
        oracle_plan=repair,
        metadata=metadata,
    )
    return BuiltEnhancement(case=case, core=core, reference=reference)


def load_m01_evidence(dataset_root: Path, seed_id: str) -> dict[str, Any]:
    """Load one existing, human-verified M01 evidence record.

    This is source provenance, not a provider call.  The builder still checks
    every claim against the current clean seed and description before emitting
    a case.
    """

    path = dataset_root / "ground_truth" / "single" / "M01" / f"{seed_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"M01 evidence not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("operators") != ["M01"]:
        raise ValueError(f"evidence is not a single M01 record: {path}")
    defects = payload.get("defects") or []
    anchor = payload.get("anchor") or (defects[0].get("anchor") if defects else None)
    if not isinstance(anchor, Mapping):
        raise ValueError(f"M01 evidence has no anchor: {path}")
    lines = anchor.get("description_lines") or []
    if not lines:
        raise ValueError(f"M01 evidence has no source line: {path}")
    target_ids = payload.get("expected_elements") or []
    relation = anchor.get("relation")
    if not target_ids or not isinstance(relation, Mapping):
        raise ValueError(f"M01 evidence has no target relation: {path}")
    return {
        "operator": "M01",
        "expected_element_ids": [str(item) for item in target_ids],
        "source_lines": lines,
        "relation": dict(relation),
        "source_ground_truth": path.relative_to(dataset_root).as_posix(),
        "verified_source_record": bool(payload.get("human_verified")),
    }


def build_m01_case(
    reference: BpmnDiagram,
    description: str,
    *,
    case_id: str,
    seed_id: str,
    evidence: Mapping[str, Any],
    core_path: str,
    reference_path: str,
    source_metadata: Mapping[str, Any] | None = None,
    verify_tier2: bool = True,
) -> BuiltEnhancement:
    """Build one conservative M01 refinement case in memory.

    ``oracle_plan`` is a restoration plan, not a provider-produced answer.  It
    uses only production atomic edit operations and is replayable by the audit.
    """

    converter = PydanticConverter()
    _require_clean(reference, "reference")
    target_ids = [str(item) for item in evidence.get("expected_element_ids", [])]
    if len(target_ids) != 1:
        raise ValueError("M01 refinement requires exactly one expected task")
    target_id = target_ids[0]
    process, node = _find_node(reference, target_id)
    if node.type not in _TASK_TYPES:
        raise ValueError(f"M01 target is not a task: {target_id}")
    if node.event_definitions or node.extra:
        raise ValueError(f"M01 target has unsupported attributes: {target_id}")

    incoming = sorted(
        (flow for flow in process.sequence_flows if flow.target_ref == target_id),
        key=lambda flow: flow.id,
    )
    outgoing = sorted(
        (flow for flow in process.sequence_flows if flow.source_ref == target_id),
        key=lambda flow: flow.id,
    )
    if len(incoming) != 1 or len(outgoing) != 1:
        raise ValueError(
            f"M01 target must be a single-path task: {target_id} "
            f"(incoming={len(incoming)}, outgoing={len(outgoing)})"
        )
    incoming_flow, outgoing_flow = incoming[0], outgoing[0]
    if outgoing_flow.condition_expression:
        raise ValueError(f"M01 target would leave a condition residue: {target_id}")

    source_lines = _source_lines(evidence)
    description_lines = description.splitlines()
    for source_line in source_lines:
        if source_line.line < 1 or source_line.line > len(description_lines):
            raise ValueError(f"source line is outside description: {source_line.line}")
        if description_lines[source_line.line - 1] != source_line.text:
            raise ValueError(f"source line evidence changed: {source_line.line}")
    if len(source_lines) != 1:
        raise ValueError("this deterministic slice holds out exactly one sentence")
    instruction = source_lines[0].text
    d_core = "\n".join(
        text
        for index, text in enumerate(description_lines, start=1)
        if index != source_lines[0].line
    )

    bridge = AddFlowOp(
        process_id=process.id,
        id=outgoing_flow.id,
        source_ref=incoming_flow.source_ref,
        target_ref=outgoing_flow.target_ref,
        name=outgoing_flow.name,
        condition_expression=outgoing_flow.condition_expression,
    )
    deletion = RemoveNodeOp(id=target_id, cascade=True)
    core, deletion_results = apply_edit_ops([deletion, bridge], reference)
    if not all(result.applied for result in deletion_results):
        raise ValueError(f"M01 deletion did not apply: {target_id}")
    _require_clean(core, "core")

    oracle_plan: list[AtomicEditOp] = [
        RemoveFlowOp(id=outgoing_flow.id),
        AddNodeOp(
            id=node.id,
            node_type=node.type,
            process_id=process.id,
            name=node.name,
        ),
        _add_flow(process.id, incoming_flow),
        _add_flow(process.id, outgoing_flow),
    ]
    restored, results = apply_edit_ops(oracle_plan, core)
    if not all(result.applied for result in results):
        raise ValueError(f"M01 oracle did not apply: {target_id}")
    if _normalise(restored) != _normalise(reference):
        raise ValueError(f"M01 oracle does not restore reference: {target_id}")

    reference_ids = set(reference.element_ids())
    core_ids = set(core.element_ids())
    if not core_ids <= reference_ids:
        raise ValueError(f"core has IDs absent from reference: {target_id}")
    removed_ids = sorted(reference_ids - core_ids)
    measurable_core_ids = {
        element.id
        for item in core.processes
        for element in (*item.flow_nodes, *item.sequence_flows)
    }
    preserved_ids = sorted(measurable_core_ids - {outgoing_flow.id})
    verification = _verification(
        reference,
        core,
        restored,
        converter,
        verify_tier2=verify_tier2,
    )
    if not all(
        value is True
        for key, value in verification.items()
        if key not in {"tier2_reference", "tier2_core"}
    ):
        raise ValueError(f"M01 construction gates failed: {target_id}")
    if verify_tier2 and not (
        verification["tier2_reference"] is True and verification["tier2_core"] is True
    ):
        raise ValueError(f"M01 core/reference are not sound: {target_id}")

    metadata = {
        "operator": "M01",
        "expected_finding": "missing_step",
        "source_seed": seed_id,
        "relation_type": "exists_task",
        "bridge_flow_id": outgoing_flow.id,
        "removed_task_id": target_id,
        "verification": verification,
        **dict(source_metadata or {}),
    }
    case = EnhancementCase(
        case_id=case_id,
        seed_id=seed_id,
        core=core_path,
        reference=reference_path,
        instruction=instruction,
        d_core=d_core,
        d_extra=instruction,
        source_lines=source_lines,
        relation=dict(evidence["relation"]),
        expected_element_ids=[target_id],
        removed_element_ids=removed_ids,
        restored_element_ids=sorted(reference_ids),
        preserved_element_ids=preserved_ids,
        oracle_plan=oracle_plan,
        metadata=metadata,
    )
    return BuiltEnhancement(case=case, core=core, reference=reference)


def build_enhancement_dataset(
    *,
    dataset_root: Path = Path("data/eval/v1.0"),
    out: Path = Path("data/eval/enhancement"),
    seed_ids: Sequence[str] | None = None,
    operator_seeds: Mapping[str, Sequence[str]] | None = None,
    dataset_version: str = "enhancement-v0.2",
    verify_tier2: bool = True,
) -> EnhancementManifest:
    """Build the deterministic E8 slice and write it byte-stably.

    Re-running with the same inputs is idempotent: an existing output is
    accepted only when every generated byte is identical.

    ``seed_ids`` selects an M01-only subset. Omitting it selects the versioned
    E8 operator mapping.
    """

    converter = PydanticConverter()
    artifacts: dict[str, bytes] = {}
    built: list[BuiltEnhancement] = []
    if operator_seeds is not None and seed_ids is not None:
        raise ValueError("pass either seed_ids or operator_seeds, not both")
    selected: Mapping[str, Sequence[str]] = (
        {"M01": seed_ids}
        if seed_ids is not None
        else operator_seeds or DEFAULT_OPERATOR_SEEDS
    )
    for operator in sorted(selected):
        for seed_id in sorted({str(item) for item in selected[operator]}):
            reference_path = dataset_root / "seeds" / f"{seed_id}.bpmn"
            description_path = dataset_root / "descriptions" / "seeds" / f"{seed_id}.txt"
            reference_bytes = reference_path.read_bytes()
            reference, unsupported = converter.parse_with_diagnostics(reference_bytes)
            if unsupported:
                raise ValueError(f"reference is lossy for seed {seed_id}")
            description = description_path.read_text(encoding="utf-8")
            if operator == "M01":
                evidence = load_m01_evidence(dataset_root, seed_id)
                case_id = f"M01-refine-{seed_id}"
                relative_dir = Path("candidates") / "M01" / seed_id
                built_case = build_m01_case(
                    reference,
                    description,
                    case_id=case_id,
                    seed_id=seed_id,
                    evidence=evidence,
                    core_path=(relative_dir / "core.bpmn").as_posix(),
                    reference_path=(relative_dir / "reference.bpmn").as_posix(),
                    source_metadata={
                        "source_dataset": "data/eval/v1.0",
                        "source_reference": f"seeds/{seed_id}.bpmn",
                        "source_description": f"descriptions/seeds/{seed_id}.txt",
                        "source_ground_truth": evidence["source_ground_truth"],
                        "source_human_verified": evidence["verified_source_record"],
                    },
                    verify_tier2=verify_tier2,
                )
            else:
                evidence = load_verified_evidence(dataset_root, operator, seed_id)
                case_id = f"{operator}-refine-{seed_id}"
                relative_dir = Path("candidates") / operator / seed_id
                built_case = build_verified_case(
                    reference,
                    description,
                    operator=operator,
                    case_id=case_id,
                    seed_id=seed_id,
                    evidence=evidence,
                    core_path=(relative_dir / "core.bpmn").as_posix(),
                    reference_path=(relative_dir / "reference.bpmn").as_posix(),
                    source_metadata={
                        "source_dataset": "data/eval/v1.0",
                        "source_reference": f"seeds/{seed_id}.bpmn",
                        "source_variant": f"variants/single/{operator}/{seed_id}.bpmn",
                        "source_description": f"descriptions/seeds/{seed_id}.txt",
                    },
                    verify_tier2=verify_tier2,
                )
            built.append(built_case)
            artifacts[(relative_dir / "reference.bpmn").as_posix()] = reference_bytes
            artifacts[(relative_dir / "core.bpmn").as_posix()] = converter.serialize(
                built_case.core
            )

    cases_payload = {
        "schema_version": "enhancement-cases@1",
        "cases": [
            case.case.model_dump(mode="json")
            for case in sorted(built, key=lambda item: item.case.case_id)
        ],
    }
    artifacts["cases.json"] = _json_bytes(cases_payload)
    files = [
        {"path": path, "sha256": _sha256(payload)}
        for path, payload in sorted(artifacts.items())
    ]
    manifest = EnhancementManifest(
        dataset_version=dataset_version,
        source={
            "dataset": "PMo Dataset",
            "dataset_version": "v1.0",
            "source_root": dataset_root.as_posix(),
            "construction_model": "gpt-5.6-luna",
            "construction_reasoning": "high",
            "providers_called": False,
        },
        construction={
            "operators": sorted(selected),
            "description_split": "verified anchor lines held out as D_extra",
            "cases_requested": sum(len(values) for values in selected.values()),
            "cases_emitted": len(built),
            "parallelism": "omitted_no_verified_replayable_semantic_case",
            "verification": {
                "automatic": "pass",
                "tier2_checked": verify_tier2,
            },
        },
        cases=[case.case.case_id for case in sorted(built, key=lambda item: item.case.case_id)],
        files=files,
    )
    artifacts["manifest.json"] = _json_bytes(manifest.model_dump(mode="json"))
    _write_idempotent(out, artifacts)
    return manifest


def _source_lines(evidence: Mapping[str, Any]) -> list[SourceLine]:
    values = evidence.get("source_lines") or evidence.get("description_lines") or []
    result = [
        SourceLine(line=int(item["line"]), text=str(item["text"]))
        for item in values
        if isinstance(item, Mapping) and "line" in item and "text" in item
    ]
    if not result:
        raise ValueError("evidence has no usable source lines")
    return sorted(result, key=lambda item: item.line)


def _find_node(diagram: BpmnDiagram, node_id: str):
    for process in diagram.processes:
        for node in process.flow_nodes:
            if node.id == node_id:
                return process, node
    raise ValueError(f"M01 target does not exist: {node_id}")


def _element_projection(diagram: BpmnDiagram, element_id: str) -> dict[str, Any] | None:
    for process in diagram.processes:
        for node in process.flow_nodes:
            if node.id == element_id:
                value = node.model_dump(mode="json")
                value.pop("bounds", None)
                value.pop("label_bounds", None)
                value["incoming"] = sorted(value.get("incoming", []))
                value["outgoing"] = sorted(value.get("outgoing", []))
                return {"kind": "node", "value": value}
        for flow in process.sequence_flows:
            if flow.id == element_id:
                value = flow.model_dump(mode="json")
                value.pop("waypoints", None)
                value.pop("label_bounds", None)
                return {"kind": "flow", "value": value}
    return None


def _add_flow(process_id: str, flow) -> AddFlowOp:
    return AddFlowOp(
        process_id=process_id,
        id=flow.id,
        source_ref=flow.source_ref,
        target_ref=flow.target_ref,
        name=flow.name,
        condition_expression=flow.condition_expression,
    )


def _require_clean(diagram: BpmnDiagram, label: str) -> None:
    issues = [
        issue
        for issue in validate(diagram).issues
        if issue.tier == ValidationTier.TIER1
    ]
    if issues:
        raise ValueError(f"{label} has tier-1 findings: {sorted({i.rule_id for i in issues})}")


def _verification(
    reference: BpmnDiagram,
    core: BpmnDiagram,
    restored: BpmnDiagram,
    converter: PydanticConverter,
    *,
    verify_tier2: bool,
) -> dict[str, bool | str]:
    def round_trip(diagram: BpmnDiagram) -> bool:
        payload = converter.serialize(diagram)
        reparsed, unsupported = converter.parse_with_diagnostics(payload)
        return not unsupported and _normalise(diagram) == _normalise(reparsed)

    result: dict[str, bool | str] = {
        "reference_round_trip": round_trip(reference),
        "core_round_trip": round_trip(core),
        "reference_tier1": not _tier1_issues(reference),
        "core_tier1": not _tier1_issues(core),
        "oracle_application": True,
        "restores_reference": _normalise(restored) == _normalise(reference),
    }
    if verify_tier2:
        from app.validation.checkers import run_woflan

        result["tier2_reference"] = not run_woflan(reference)
        result["tier2_core"] = not run_woflan(core)
    else:
        result["tier2_reference"] = "not_run"
        result["tier2_core"] = "not_run"
    return result


def _tier1_issues(diagram: BpmnDiagram) -> list[Any]:
    return [issue for issue in validate(diagram).issues if issue.tier == ValidationTier.TIER1]


def _normalise(diagram: BpmnDiagram) -> BpmnDiagram:
    result = diagram.model_copy(deep=True)
    result.namespaces = {}
    for process in result.processes:
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
    result.processes.sort(key=lambda process: process.id)
    return result


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n").encode(
        "utf-8"
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_idempotent(out: Path, artifacts: Mapping[str, bytes]) -> None:
    expected = {Path(path).as_posix(): payload for path, payload in artifacts.items()}
    if out.exists():
        actual_paths = {
            path.relative_to(out).as_posix(): path.read_bytes()
            for path in out.rglob("*")
            if path.is_file()
        }
        if actual_paths == expected:
            return
        raise FileExistsError(f"refusing to overwrite non-identical enhancement output: {out}")
    for relative, payload in expected.items():
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
