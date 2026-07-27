"""Reusable repair orchestration for CLI and future API routes."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.experiments import ExperimentConfig, RepairMode
from app.history import service as hist
from app.llm import client as llm_client
from app.llm.prompt_context import render_prompt_template
from app.llm.router import TaskType, resolve_model, resolve_provider
from app.llm.schema import strict_json_schema
from app.model.schema import BpmnDiagram
from app.repair.ops import (
    AtomicEditOpsResult,
    EditOp,
    ReplaceDiagramOp,
    apply_edit_ops,
    atomic_edit_op_list_adapter,
)
from app.repair.quick_fixes import propose_quick_fix
from app.services.ir_payload import (
    call_with_ir_correction,
    diagram_payload,
    parse_diagram_payload,
)
from app.services.validation import validate_diagram
from app.validation.rules import ValidationIssue, issue_to_dict

_PROMPT_DIR = Path(__file__).parent.parent / "llm" / "prompts"
_REPAIR_PROMPT = _PROMPT_DIR / "repair.txt"
_ATOMIC_REPAIR_PROMPT = _PROMPT_DIR / "repair_atomic.txt"
_XML_REPAIR_PROMPT = _PROMPT_DIR / "repair_xml.txt"

# computed once — the EditOp union has no open dicts, so it is fully strict-expressible
_ATOMIC_OPS_SCHEMA = strict_json_schema(AtomicEditOpsResult)


class UnresolvedRepair(BaseModel):
    rule_id: str | None = None
    reason: str


class RepairResult(BaseModel):
    repaired_diagram: BpmnDiagram
    unresolved: list[UnresolvedRepair] = []
    rev_id: str | None = None
    session_id: str | None = None


class DispatcherRepairResult(BaseModel):
    repaired_diagram: BpmnDiagram
    applied_ops: list[EditOp] = []
    remaining_issues: list[ValidationIssue] = []
    iterations: int = 0
    converged: bool = False


RepairFn = Callable[..., Awaitable[RepairResult]]
AtomicRepairFn = Callable[..., Awaitable[list[EditOp]]]
AtomicEditOpList = list[EditOp]


async def repair_diagram(
    diagram: BpmnDiagram,
    issues: list[ValidationIssue],
    session_id: str | None = None,
    config: ExperimentConfig | None = None,
    snapshot: bool = True,
) -> RepairResult:
    """repair a diagram using the existing repair prompt and issue list"""
    payload: dict[str, Any] = {
        "ir_format": str((config or ExperimentConfig()).ir_format),
        "diagram": diagram_payload(diagram, config),
        "issues": [issue_to_dict(issue) for issue in issues],
    }
    async def attempt(feedback: str | None) -> tuple[BpmnDiagram, list[UnresolvedRepair]]:
        prompt = json.dumps(payload)
        if feedback:
            prompt = f"{prompt}\n\n{feedback}"
        raw = await llm_client.complete(
            prompt=prompt,
            system=render_prompt_template(_REPAIR_PROMPT, config=config),
            model=resolve_model(TaskType.REPAIR, config=config),
            provider=resolve_provider(TaskType.REPAIR, config=config),
            reasoning_effort=str(config.reasoning_effort)
            if config and config.reasoning_effort
            else None,
        )
        parsed = json.loads(raw)
        return (
            parse_diagram_payload(parsed["ir"], config),
            [_normalise_unresolved(item) for item in parsed.get("unresolved", [])],
        )

    repaired_diagram, unresolved = await call_with_ir_correction(attempt)

    if not snapshot:
        return RepairResult(repaired_diagram=repaired_diagram, unresolved=unresolved)

    active_session_id = session_id
    new_session_id: str | None = None
    if active_session_id is None:
        active_session_id = hist.create_session()
        new_session_id = active_session_id

    revision = hist.snapshot(
        session_id=active_session_id,
        diagram=repaired_diagram,
        message="llm repair",
        author="llm",
    )

    return RepairResult(
        repaired_diagram=repaired_diagram,
        unresolved=unresolved,
        rev_id=revision.rev_id,
        session_id=new_session_id,
    )


async def repair_raw_xml(
    xml: str,
    instruction: str | None = None,
    config: ExperimentConfig | None = None,
) -> str:
    """fix unparseable BPMN XML text-to-text, returning corrected XML

    used when a file cannot be parsed into the IR (e.g. duplicate element IDs),
    so the structured edit-op repair loop is unavailable. the LLM rewrites the
    raw XML directly; the caller is responsible for re-parsing the result.
    """
    prompt = xml if not instruction else f"{xml}\n\n## User instruction\n{instruction}"
    raw = await llm_client.complete(
        prompt=prompt,
        system=render_prompt_template(_XML_REPAIR_PROMPT, config=config),
        model=resolve_model(TaskType.REPAIR, config=config),
        provider=resolve_provider(TaskType.REPAIR, config=config),
        max_tokens=16384,
        reasoning_effort=str(config.reasoning_effort)
        if config and config.reasoning_effort
        else None,
    )
    return _strip_code_fences(raw)


def _strip_code_fences(text: str) -> str:
    """drop a leading/trailing markdown fence if the model wrapped its output"""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


async def repair_with_edit_ops(
    diagram: BpmnDiagram,
    issues: list[ValidationIssue],
    config: ExperimentConfig | None = None,
    context_issues: list[ValidationIssue] | None = None,
) -> AtomicEditOpList:
    """repair a diagram by asking the LLM for atomic EditOps

    `context_issues` are the other issues open on the same diagram — typically
    warnings the loop will not act on. They are passed as background so the fix
    for `issues` does not walk straight into a known problem, not as work to do.
    """
    payload: dict[str, Any] = {
        "ir_format": str((config or ExperimentConfig()).ir_format),
        "diagram": diagram_payload(diagram, config),
        "issues": [issue_to_dict(issue) for issue in issues],
        "repair_mode": RepairMode.ATOMIC,
        "id_constraints": _diagram_id_constraints(diagram),
    }
    if context_issues:
        payload["other_open_issues"] = [
            issue_to_dict(issue) for issue in context_issues
        ]
    response_schema = _atomic_ops_schema_for_diagram(diagram)
    # structured outputs constrain the reply to the EditOp schema, so no fenced
    # scraping or best-effort JSON repair is needed
    parsed = await llm_client.complete_structured(
        prompt=json.dumps(payload),
        schema=response_schema,
        system=render_prompt_template(
            _ATOMIC_REPAIR_PROMPT,
            config=config,
            replacements_override={
                "{{ATOMIC_EDIT_OP_SCHEMA}}": _json_schema_block(response_schema),
            },
        ),
        model=resolve_model(TaskType.REPAIR, config=config),
        provider=resolve_provider(TaskType.REPAIR, config=config),
        reasoning_effort=str(config.reasoning_effort)
        if config and config.reasoning_effort
        else None,
    )
    ops_data = parsed.get("ops", parsed) if isinstance(parsed, dict) else parsed
    _validate_atomic_op_ids(ops_data, diagram)
    return list(atomic_edit_op_list_adapter.validate_python(ops_data))


async def dispatch_repair(
    diagram: BpmnDiagram,
    issues: list[ValidationIssue],
    config: ExperimentConfig | None = None,
    repair_fn: RepairFn | None = None,
    atomic_repair_fn: AtomicRepairFn | None = None,
) -> DispatcherRepairResult:
    """run the closed repair loop until convergence or iteration cap"""
    active_config = config or ExperimentConfig()
    active_repair_fn = repair_fn or repair_diagram
    active_atomic_repair_fn = atomic_repair_fn or repair_with_edit_ops
    current = diagram.model_copy(deep=True)
    remaining = list(issues)
    applied_ops: list[EditOp] = []
    iterations = 0

    while iterations < active_config.max_repair_iters:
        issue = _highest_priority_issue(remaining)
        if issue is None:
            break

        if active_config.repair_mode == RepairMode.REGEN:
            result = await active_repair_fn(
                current,
                issues=[issue],
                config=active_config,
                snapshot=False,
            )
            current = result.repaired_diagram
            applied_ops.append(ReplaceDiagramOp(diagram=current))
        else:
            quick_fix_ops = propose_quick_fix(issue, current)
            ops = quick_fix_ops
            if ops is None:
                ops = await active_atomic_repair_fn(
                    current,
                    issues=[issue],
                    config=active_config,
                    context_issues=[other for other in remaining if other is not issue],
                )
            current, op_results = apply_edit_ops(ops, current)
            applied_ops.extend(result.op for result in op_results if result.applied)

        validation = await validate_diagram(
            current,
            include_semantic=active_config.tiers_enabled.t3,
            config=active_config,
        )
        remaining = validation.issues + validation.semantic_issues
        iterations += 1

    return DispatcherRepairResult(
        repaired_diagram=current,
        applied_ops=applied_ops,
        remaining_issues=remaining,
        iterations=iterations,
        converged=_has_no_errors(remaining),
    )


def repair_prompt_name(config: ExperimentConfig | None = None) -> str:
    return repair_prompt_path(config).name


def repair_prompt_path(config: ExperimentConfig | None = None) -> Path:
    if config is not None and config.repair_mode == RepairMode.ATOMIC:
        return _ATOMIC_REPAIR_PROMPT
    return _REPAIR_PROMPT


def xml_repair_prompt_path() -> Path:
    return _XML_REPAIR_PROMPT


def _atomic_ops_schema_for_diagram(diagram: BpmnDiagram) -> dict[str, Any]:
    schema = deepcopy(_ATOMIC_OPS_SCHEMA)
    constraints = _diagram_id_constraints(diagram)
    node_ids = constraints["node_ids"]
    flow_ids = constraints["flow_ids"]
    process_ids = constraints["process_ids"]

    for def_name, field_name, enum_values in [
        ("RemoveNodeOp", "id", node_ids),
        ("RenameNodeOp", "id", node_ids),
        ("ChangeNodeTypeOp", "id", node_ids),
        ("ChangeGatewayTypeOp", "id", node_ids),
        ("RemoveFlowOp", "id", flow_ids),
        ("RenameFlowOp", "id", flow_ids),
        ("SetConditionOp", "flow_id", flow_ids),
        ("AddNodeOp", "process_id", process_ids),
    ]:
        _set_schema_enum(schema, def_name, field_name, enum_values)

    # add_flow endpoints stay open strings. Enumerating them against the incoming
    # diagram would make add_node unusable — a node created earlier in the same
    # op list could never be connected, so every added node would be an orphan.
    # _validate_atomic_op_ids walks the list in order and rejects genuinely
    # unknown ids, which is the guarantee the enum was there to provide.
    for field_name in ("source_ref", "target_ref"):
        _describe_open_id_field(schema, "AddFlowOp", field_name, node_ids)

    return schema


def _describe_open_id_field(
    schema: dict[str, Any],
    def_name: str,
    field_name: str,
    existing_ids: list[str],
) -> None:
    """leave a field unconstrained but tell the model what it may reference"""
    field_schema = schema.get("$defs", {}).get(def_name, {}).get("properties", {}).get(field_name)
    if field_schema is None:
        return
    listed = ", ".join(existing_ids) if existing_ids else "<none>"
    field_schema["description"] = (
        f"{field_name}. Either an existing node ID ({listed}) or the ID of a node "
        "created by an earlier add_node op in this same ops list."
    )


def _json_schema_block(schema: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(schema, indent=2, sort_keys=True) + "\n```"


def _set_schema_enum(
    schema: dict[str, Any],
    def_name: str,
    field_name: str,
    enum_values: list[str],
) -> None:
    if not enum_values:
        return
    field_schema = schema["$defs"][def_name]["properties"][field_name]
    field_schema["enum"] = enum_values
    field_schema["description"] = f"{field_schema.get('description', field_name)}. "


def _diagram_id_constraints(diagram: BpmnDiagram) -> dict[str, list[str]]:
    return {
        "process_ids": sorted(proc.id for proc in diagram.processes),
        "node_ids": sorted(
            node.id for proc in diagram.processes for node in proc.flow_nodes
        ),
        "flow_ids": sorted(
            flow.id for proc in diagram.processes for flow in proc.sequence_flows
        ),
    }


def _validate_atomic_op_ids(ops_data: Any, diagram: BpmnDiagram) -> None:
    """reject ops referencing ids that will not exist when the op is applied

    Ops apply in order, so the set of valid ids is walked forward with them: a
    node introduced by an earlier `add_node` is a legal endpoint for a later
    `add_flow`, and one removed by an earlier `remove_node` is not. Pinning the
    check to the diagram as it arrived would make `add_node` useless — a new node
    could never be connected to anything, which is a defect in itself.
    """
    if not isinstance(ops_data, list):
        return

    constraints = _diagram_id_constraints(diagram)
    node_ids = set(constraints["node_ids"])
    flow_ids = set(constraints["flow_ids"])
    process_ids = set(constraints["process_ids"])

    for index, op_data in enumerate(ops_data):
        if not isinstance(op_data, dict):
            continue
        op = op_data.get("op")
        if op in {
            "remove_node",
            "rename_node",
            "change_node_type",
            "change_gateway_type",
        }:
            _require_id_in_enum(
                op_data.get("id"),
                node_ids,
                "node",
                index,
                "id",
                sorted(node_ids),
            )
            if op == "remove_node":
                node_ids.discard(op_data.get("id"))
        elif op == "add_flow":
            _require_id_in_enum(
                op_data.get("source_ref"),
                node_ids,
                "node",
                index,
                "source_ref",
                sorted(node_ids),
            )
            _require_id_in_enum(
                op_data.get("target_ref"),
                node_ids,
                "node",
                index,
                "target_ref",
                sorted(node_ids),
            )
            _register_new_id(op_data.get("id"), flow_ids)
        elif op in {"remove_flow", "rename_flow"}:
            _require_id_in_enum(
                op_data.get("id"),
                flow_ids,
                "flow",
                index,
                "id",
                sorted(flow_ids),
            )
            if op == "remove_flow":
                flow_ids.discard(op_data.get("id"))
        elif op == "set_condition":
            _require_id_in_enum(
                op_data.get("flow_id"),
                flow_ids,
                "flow",
                index,
                "flow_id",
                sorted(flow_ids),
            )
        elif op == "add_node":
            _require_id_in_enum(
                op_data.get("process_id"),
                process_ids,
                "process",
                index,
                "process_id",
                sorted(process_ids),
            )
            # an add_node onto an id that is already taken is not an addition —
            # it is a no-op the model reached for instead of remove_node. The
            # apply layer rejects it too, but only after the round trip.
            _reject_taken_id(op_data.get("id"), node_ids | flow_ids, index)
            # the new node becomes a legal endpoint for later ops in this list
            _register_new_id(op_data.get("id"), node_ids)


def _reject_taken_id(value: Any, known: set[str], index: int) -> None:
    """refuse to introduce an id that already exists in the diagram"""
    if isinstance(value, str) and value in known:
        raise ValueError(
            f"op[{index}] add_node id '{value}' already exists — use remove_node "
            "or add_flow to act on an existing element."
        )


def _register_new_id(value: Any, known: set[str]) -> None:
    """record an id an op introduces so later ops in the same list may use it"""
    if isinstance(value, str) and value:
        known.add(value)


def _require_id_in_enum(
    value: Any,
    allowed: set[str],
    kind: str,
    index: int,
    field_name: str,
    allowed_values: list[str],
) -> None:
    if not isinstance(value, str) or value in allowed:
        return
    options = ", ".join(allowed_values) if allowed_values else "<none>"
    raise ValueError(
        f"Invalid repair operation at ops[{index}].{field_name}: "
        f"expected an existing {kind} ID, got '{value}'. "
        f"Allowed {kind} IDs: {options}."
    )


def _highest_priority_issue(issues: list[ValidationIssue]) -> ValidationIssue | None:
    actionable = [issue for issue in issues if issue.severity == "error"]
    if not actionable:
        return None
    return sorted(actionable, key=lambda issue: _severity_rank(issue), reverse=True)[0]


def _severity_rank(issue: ValidationIssue) -> int:
    ranks = {"error": 3, "warning": 2, "info": 1}
    return ranks.get(str(issue.severity), 0)


def _has_no_errors(issues: list[ValidationIssue]) -> bool:
    return not any(issue.severity == "error" for issue in issues)


def _normalise_unresolved(item: object) -> UnresolvedRepair:
    if isinstance(item, str):
        return UnresolvedRepair(reason=item)
    if isinstance(item, dict):
        rule_id = item.get("rule_id") or item.get("id")
        reason = item.get("reason") or item.get("message")
        return UnresolvedRepair(
            rule_id=str(rule_id) if rule_id is not None else None,
            reason=str(reason) if reason else json.dumps(item),
        )
    return UnresolvedRepair(reason=str(item))
