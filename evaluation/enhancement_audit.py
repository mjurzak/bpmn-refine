"""Provider-free audit of E8 construction artifacts."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from app.model.formats.pydantic_ir import PydanticConverter
from app.model.schema import BpmnDiagram, FlowNodeType
from app.repair.ops import AtomicEditOp, apply_edit_ops, edit_op_list_adapter
from app.validation.rules import Severity, ValidationTier, validate


AUDIT_VERSION = "enhancement-audit@1"
GateStatus = Literal["pass", "fail", "not_run"]
Tier2Mode = Literal["auto", "on", "off"]
Tier2Runner = Callable[[BpmnDiagram], Any]


@dataclass(frozen=True)
class GateResult:
    status: GateStatus
    codes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"status": self.status}
        if self.codes:
            result["codes"] = list(self.codes)
        return result


@dataclass(frozen=True)
class CaseAudit:
    case_id: str
    automatic_status: str
    gates: dict[str, GateResult]

    def as_dict(self) -> dict[str, Any]:
        failed = [name for name, gate in self.gates.items() if gate.status == "fail"]
        skipped = [
            name for name, gate in self.gates.items() if gate.status == "not_run"
        ]
        return {
            "case_id": self.case_id,
            "automatic_status": self.automatic_status,
            "failed_gates": failed,
            "skipped_gates": skipped,
            "gates": {name: gate.as_dict() for name, gate in self.gates.items()},
        }


@dataclass(frozen=True)
class AuditSummary:
    cases: tuple[CaseAudit, ...]

    def as_dict(self) -> dict[str, Any]:
        automatic = Counter(case.automatic_status for case in self.cases)
        gates = Counter(
            gate.status for case in self.cases for gate in case.gates.values()
        )
        if automatic["fail"]:
            overall = "automatic_fail"
        elif automatic["pass_with_skips"]:
            overall = "automatic_pass_with_skips"
        else:
            overall = "automatic_pass"
        return {
            "audit_version": AUDIT_VERSION,
            "overall_status": overall,
            "automatic_status_counts": dict(sorted(automatic.items())),
            "gate_status_counts": dict(sorted(gates.items())),
            "counts": {
                "cases": len(self.cases),
                "automatic_pass": automatic["pass"],
                "automatic_pass_with_skips": automatic["pass_with_skips"],
                "automatic_fail": automatic["fail"],
            },
            "cases": [case.as_dict() for case in self.cases],
        }


class _ArtifactError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else str(value or "")


def audit_case(
    case: Any,
    *,
    root: Path | None = None,
    tier2: Tier2Mode = "auto",
    tier2_runner: Tier2Runner | None = None,
    required_metadata: Sequence[str] | None = None,
) -> CaseAudit:
    """Run deterministic construction gates for one case."""

    record = _record_mapping(case)
    metadata = _metadata_gate(record, required_metadata)
    case_id = _text(_lookup(record, "case_id", "variant_id", "id")[1]) or "<missing>"
    gates: dict[str, GateResult] = {"required_metadata": metadata}

    core, core_gate = _load_diagram(
        _lookup(record, "core", "core_model", "core_path", "core_xml", "m_core", "input")[1],
        root,
        "core",
    )
    reference, reference_gate = _load_diagram(
        _lookup(
            record,
            "reference",
            "reference_model",
            "reference_path",
            "reference_xml",
            "target",
            "ground_truth",
            "seed_model",
        )[1],
        root,
        "reference",
    )
    gates["core_parse"] = core_gate
    gates["reference_parse"] = reference_gate

    gates["core_round_trip"] = _round_trip_gate(core)
    gates["reference_round_trip"] = _round_trip_gate(reference)
    gates["core_tier1"] = _tier1_gate(core)
    gates["reference_tier1"] = _tier1_gate(reference)

    removed = _removed_ids(record)
    added = _added_ids(record)
    changed = _changed_ids(record)
    gates["no_dangling_residue"] = _residue_gate(core, reference, removed, added, changed)

    plan, plan_gate = _parse_plan(
        _lookup(record, "oracle_plan", "oracle", "plan", "operations", "ops")[1]
    )
    gates["oracle_plan"] = plan_gate
    restored = None
    if core is not None and plan is not None and plan_gate.status == "pass":
        restored, results = apply_edit_ops(plan, core)
        failed = sum(not result.applied for result in results)
        gates["oracle_application"] = (
            GateResult("pass") if not failed else GateResult("fail", ("op_not_applied",))
        )
    else:
        gates["oracle_application"] = GateResult("not_run", ("prerequisite_failed",))
    gates["restores_reference"] = _restoration_gate(restored, reference)

    gates["core_tier2"] = _tier2_gate(
        core, mode=tier2, runner=tier2_runner
    )

    automatic_status = _automatic_status(gates)
    return CaseAudit(case_id, automatic_status, gates)


def audit_cases(
    cases: Iterable[Any],
    *,
    root: Path | None = None,
    tier2: Tier2Mode = "auto",
    tier2_runner: Tier2Runner | None = None,
    required_metadata: Sequence[str] | None = None,
) -> AuditSummary:
    return AuditSummary(
        tuple(
            audit_case(
                case,
                root=root,
                tier2=tier2,
                tier2_runner=tier2_runner,
                required_metadata=required_metadata,
            )
            for case in cases
        )
    )


def load_cases(path: Path) -> list[Any]:
    if path.is_dir():
        return [
            item
            for child in sorted(path.glob("*.json"))
            for item in _cases_from_json(json.loads(child.read_text(encoding="utf-8")))
        ]
    return _cases_from_json(json.loads(path.read_text(encoding="utf-8")))


def _cases_from_json(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        for key in ("cases", "enhancements", "records", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return [payload]
    raise ValueError("case input must be a JSON object or list")


def _record_mapping(case: Any) -> dict[str, Any]:
    if isinstance(case, Mapping):
        return dict(case)
    if isinstance(case, BaseModel):
        return case.model_dump(mode="python", by_alias=True)
    if is_dataclass(case):
        return asdict(case)
    if hasattr(case, "__dict__"):
        return dict(vars(case))
    raise TypeError("enhancement case must be a mapping, Pydantic model, or dataclass")


def _lookup(record: Mapping[str, Any], *names: str) -> tuple[bool, Any]:
    metadata = record.get("metadata")
    nested = metadata if isinstance(metadata, Mapping) else {}
    for name in names:
        if name in record and record[name] is not None:
            return True, record[name]
        if name in nested and nested[name] is not None:
            return True, nested[name]
    return False, None


def _metadata_gate(
    record: Mapping[str, Any], required_metadata: Sequence[str] | None
) -> GateResult:
    required = tuple(
        required_metadata
        or (
            "case_id",
            "seed_id",
            "instruction",
            "d_core",
            "d_extra",
            "relation",
            "expected_element_ids",
            "restored_element_ids",
            "preserved_element_ids",
            "oracle_plan",
            "removed_element_ids",
            "source_record",
            "verification_status",
        )
    )
    aliases = {
        "case_id": ("case_id", "variant_id", "id"),
        "seed_id": ("seed_id", "seed", "source_seed", "reference_seed"),
        "instruction": (
            "instruction",
            "description_extra",
            "d_extra",
            "extra_description",
        ),
        "oracle_plan": ("oracle_plan", "oracle", "plan", "operations", "ops"),
        "removed_element_ids": (
            "removed_element_ids",
            "removed_ids",
            "deleted_element_ids",
            "realized_element_ids",
            "realised_element_ids",
        ),
        "source_record": ("source_record", "source_ground_truth"),
        "verification_status": ("verification_status", "verification"),
    }
    missing: list[str] = []
    for name in required:
        found, value = _lookup(record, *aliases.get(name, (name,)))
        if name == "removed_element_ids" and value == []:
            changed_found, changed = _lookup(record, "changed_element_ids")
            if changed_found and changed:
                continue
        if not found or value is None or value == "" or value == []:
            missing.append(name)
    return GateResult("pass" if not missing else "fail", tuple(f"missing_{item}" for item in missing))


def _load_diagram(
    value: Any, root: Path | None, label: str
) -> tuple[BpmnDiagram | None, GateResult]:
    if value is None:
        return None, GateResult("fail", (f"missing_{label}",))
    try:
        artifact = _read_artifact(value, root)
        diagram, unsupported = PydanticConverter().parse_with_diagnostics(artifact)
        if unsupported:
            return None, GateResult("fail", (f"lossy_{label}_import",))
        return diagram, GateResult("pass")
    except Exception:
        return None, GateResult("fail", (f"{label}_parse_error",))


def _read_artifact(value: Any, root: Path | None) -> bytes:
    if isinstance(value, BpmnDiagram):
        return PydanticConverter().serialize(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, Path):
        return value.read_bytes()
    if isinstance(value, str):
        if value.lstrip().startswith("<"):
            return value.encode("utf-8")
        path = Path(value)
        if root is not None and not path.is_absolute():
            path = root / path
        return path.read_bytes()
    if isinstance(value, Mapping):
        for key in ("path", "file", "filename"):
            if key in value:
                return _read_artifact(value[key], root)
        for key in ("xml", "payload", "bytes"):
            if key in value:
                return _read_artifact(value[key], root)
        candidate = value.get("diagram", value.get("model", value))
        if isinstance(candidate, Mapping) and "definitions_id" in candidate:
            return PydanticConverter().serialize(BpmnDiagram.model_validate(candidate))
    if isinstance(value, BaseModel):
        return _read_artifact(value.model_dump(mode="python"), root)
    raise _ArtifactError("unsupported_artifact")


def _round_trip_gate(diagram: BpmnDiagram | None) -> GateResult:
    if diagram is None:
        return GateResult("not_run", ("parse_failed",))
    try:
        converter = PydanticConverter()
        reparsed, unsupported = converter.parse_with_diagnostics(converter.serialize(diagram))
        if unsupported:
            return GateResult("fail", ("lossy_round_trip",))
        if _normalise(diagram) != _normalise(reparsed):
            return GateResult("fail", ("ir_changed_after_round_trip",))
        return GateResult("pass")
    except Exception:
        return GateResult("fail", ("round_trip_error",))


def _tier1_gate(diagram: BpmnDiagram | None) -> GateResult:
    if diagram is None:
        return GateResult("not_run", ("parse_failed",))
    issues = [issue for issue in validate(diagram).issues if issue.tier == ValidationTier.TIER1]
    return GateResult("pass" if not issues else "fail", tuple(sorted({issue.rule_id for issue in issues})))


def _tier2_gate(
    diagram: BpmnDiagram | None, *, mode: Tier2Mode, runner: Tier2Runner | None
) -> GateResult:
    if diagram is None:
        return GateResult("not_run", ("parse_failed",))
    if mode == "off":
        return GateResult("not_run", ("disabled",))
    if runner is None:
        try:
            from app.validation.checkers import run_woflan

            runner = run_woflan
        except (ImportError, ModuleNotFoundError):
            return GateResult("fail" if mode == "on" else "not_run", ("tier2_unavailable",))
    try:
        result = runner(diagram)
        if inspect.isawaitable(result):
            result = asyncio.run(result)
    except (ImportError, ModuleNotFoundError):
        return GateResult("fail" if mode == "on" else "not_run", ("tier2_unavailable",))
    except Exception:
        return GateResult("fail", ("tier2_error",))

    sound, codes, unknown = _tier2_result(result)
    if unknown:
        return GateResult("fail" if mode == "on" else "not_run", ("tier2_unknown",))
    if not sound:
        return GateResult("fail", tuple(codes) or ("tier2_unsound",))
    if any(code.endswith(":unsupported") or code.endswith(":runtime_error") for code in codes):
        return GateResult("fail" if mode == "on" else "not_run", tuple(codes))
    return GateResult("pass")


def _tier2_result(result: Any) -> tuple[bool, list[str], bool]:
    if isinstance(result, bool):
        return result, [], False
    if isinstance(result, Mapping):
        if "sound" in result:
            issues = result.get("issues", [])
            return bool(result["sound"]), _issue_codes(issues), False
        if "is_valid" in result:
            return bool(result["is_valid"]), _issue_codes(result.get("issues", [])), False
    if hasattr(result, "sound"):
        return bool(result.sound), _issue_codes(getattr(result, "issues", [])), False
    issues = getattr(result, "issues", result)
    if isinstance(issues, Iterable) and not isinstance(issues, (str, bytes, Mapping)):
        codes = _issue_codes(issues)
        return not any(code.endswith(":soundness") for code in codes), codes, False
    return False, [], True


def _issue_codes(issues: Any) -> list[str]:
    if issues is None:
        return []
    if isinstance(issues, Mapping):
        issues = [issues]
    if isinstance(issues, (str, bytes)):
        return [str(issues)]
    try:
        values = list(issues)
    except TypeError:
        values = [issues]
    codes: list[str] = []
    for issue in values:
        if isinstance(issue, Mapping):
            code = issue.get("rule_id", issue.get("code"))
        else:
            code = getattr(issue, "rule_id", getattr(issue, "code", None))
        if code:
            codes.append(str(code))
    return sorted(set(codes))


def _parse_plan(value: Any) -> tuple[list[AtomicEditOp] | None, GateResult]:
    if isinstance(value, Mapping):
        value = value.get("ops", value.get("operations", value.get("plan")))
    if value is None or value == []:
        return None, GateResult("fail", ("missing_oracle_plan",))
    if not isinstance(value, list):
        try:
            value = list(value)
        except TypeError:
            return None, GateResult("fail", ("oracle_plan_not_a_list",))
    try:
        plan = edit_op_list_adapter.validate_python(value)
    except (ValidationError, TypeError, ValueError):
        return None, GateResult("fail", ("invalid_oracle_plan",))
    return plan, GateResult("pass")


def _restoration_gate(
    restored: BpmnDiagram | None, reference: BpmnDiagram | None
) -> GateResult:
    if restored is None or reference is None:
        return GateResult("not_run", ("prerequisite_failed",))
    return GateResult(
        "pass" if _normalise(restored) == _normalise(reference) else "fail",
        () if _normalise(restored) == _normalise(reference) else ("reference_mismatch",),
    )


def _residue_gate(
    core: BpmnDiagram | None,
    reference: BpmnDiagram | None,
    removed: set[str],
    added: set[str] | None = None,
    changed: set[str] | None = None,
) -> GateResult:
    if core is None or reference is None:
        return GateResult("not_run", ("parse_failed",))
    core_ids = set(core.element_ids())
    reference_ids = set(reference.element_ids())
    codes: list[str] = []
    added = added or set()
    changed = changed or set()
    if not removed and not added and not changed:
        codes.append("missing_removed_element_ids")
    if removed != reference_ids - core_ids:
        codes.append("removed_id_set_does_not_match_reference_delta")
    if added != core_ids - reference_ids:
        codes.append("added_id_set_does_not_match_reference_delta")
    for process in core.processes:
        node_ids = {node.id for node in process.flow_nodes}
        for flow in process.sequence_flows:
            if flow.source_ref not in node_ids or flow.target_ref not in node_ids:
                codes.append("dangling_sequence_flow")
            if any(item in (flow.condition_expression or "") for item in removed):
                codes.append("condition_mentions_removed_id")
        for pool in process.pools:
            for lane in pool.lanes:
                if any(item not in node_ids for item in lane.flow_node_refs):
                    codes.append("dangling_lane_reference")
    return GateResult("pass" if not codes else "fail", tuple(sorted(set(codes))))


def _removed_ids(record: Mapping[str, Any]) -> set[str]:
    value = _lookup(
        record,
        "removed_element_ids",
        "removed_ids",
        "deleted_element_ids",
        "realized_element_ids",
        "realised_element_ids",
    )[1]
    if isinstance(value, str):
        return {value}
    if isinstance(value, Iterable):
        return {str(item) for item in value if str(item)}
    return set()


def _changed_ids(record: Mapping[str, Any]) -> set[str]:
    value = _lookup(record, "changed_element_ids", "changed_ids")[1]
    if isinstance(value, str):
        return {value}
    if isinstance(value, Iterable):
        return {str(item) for item in value if str(item)}
    return set()


def _added_ids(record: Mapping[str, Any]) -> set[str]:
    value = _lookup(record, "added_element_ids", "added_ids")[1]
    if isinstance(value, str):
        return {value}
    if isinstance(value, Iterable):
        return {str(item) for item in value if str(item)}
    return set()


def _automatic_status(gates: Mapping[str, GateResult]) -> str:
    if any(gate.status == "fail" for gate in gates.values()):
        return "fail"
    if any(gate.status == "not_run" for gate in gates.values()):
        return "pass_with_skips"
    return "pass"


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", type=Path, help="case JSON file or directory")
    parser.add_argument("--root", type=Path, default=None, help="base for relative artifacts")
    parser.add_argument("--tier2", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    args = parser.parse_args(argv)
    summary = audit_cases(load_cases(args.cases), root=args.root, tier2=args.tier2)
    print(json.dumps(summary.as_dict(), indent=2 if args.pretty else None, sort_keys=True))
    return 1 if summary.as_dict()["overall_status"] == "automatic_fail" else 0


if __name__ == "__main__":  # pragma: no cover - exercised through CLI tests
    raise SystemExit(main())
