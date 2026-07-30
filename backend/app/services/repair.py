"""Reusable repair orchestration for CLI and future API routes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, model_validator

from app.experiments import ExperimentConfig, RepairMode
from app.history import service as hist
from app.llm import client as llm_client
from app.llm.prompt_context import render_prompt_template
from app.llm.router import TaskType, resolve_model, resolve_provider, resolve_sampling
from app.llm.schema import strict_json_schema
from app.model.schema import BpmnDiagram
from app.repair.ops import (
    GATEWAY_NODE_TYPES,
    AtomicEditOpsResult,
    EditOp,
    EditOpResult,
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
# One initial plan plus two corrections. Invalid plans are never applied, so
# these attempts cost latency but cannot leak speculative edits into a proposal.
ATOMIC_PLAN_ATTEMPTS = 3


class UnresolvedRepair(BaseModel):
    rule_id: str | None = None
    reason: str


class RepairResult(BaseModel):
    repaired_diagram: BpmnDiagram
    unresolved: list[UnresolvedRepair] = []
    rev_id: str | None = None
    session_id: str | None = None


class StopReason(StrEnum):
    """why the repair loop stopped, so an evaluation never has to infer it"""

    CONVERGED = "converged"
    NO_PROGRESS = "no_progress"
    REPEATED_STATE = "repeated_state"
    ITERATION_BUDGET = "iteration_budget"


class OpOrigin(StrEnum):
    """what produced an operation"""

    QUICK_FIX = "quick_fix"
    MODEL_PLAN = "model_plan"
    MODEL_REGEN = "model_regen"


class DispatcherRepairResult(BaseModel):
    repaired_diagram: BpmnDiagram
    applied_ops: list[EditOp] = []
    # who produced each applied op, positionally aligned with `applied_ops`
    applied_op_origins: list[OpOrigin] = []
    # operations the apply layer rejected. They used to be dropped here while
    # `/repair/apply` returned them, so an automatic run could report a clean
    # result built from a plan that only half executed — and a minimality metric
    # computed over `applied_ops` would count the successful remainder as the
    # whole intervention.
    failed_ops: list[EditOpResult] = []
    failed_op_origins: list[OpOrigin] = []
    remaining_issues: list[ValidationIssue] = []
    iterations: int = 0
    # no repairable issue of any severity remains
    converged: bool = False
    # the weaker, historical signal: no error-severity issue remains. Kept
    # separate because the two answer different questions and Chapter 6 reports
    # both — a run can drain every error and still leave warnings standing.
    errors_resolved: bool = False
    stop_reason: StopReason = StopReason.ITERATION_BUDGET

    @model_validator(mode="after")
    def _origins_cover_every_op(self) -> DispatcherRepairResult:
        """a dropped origin is worse than none at all — it shifts every later one"""
        if len(self.applied_op_origins) != len(self.applied_ops):
            raise ValueError("applied_op_origins must align with applied_ops")
        if len(self.failed_op_origins) != len(self.failed_ops):
            raise ValueError("failed_op_origins must align with failed_ops")
        return self


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
    active_config = config or ExperimentConfig()
    payload: dict[str, Any] = {
        "ir_format": str(active_config.ir_format),
        "diagram": diagram_payload(diagram, config),
        "issues": [
            issue_to_dict(
                issue, include_formal_evidence=active_config.include_formal_evidence
            )
            for issue in issues
        ],
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
            **resolve_sampling(config),
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
        **resolve_sampling(config),
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
    """repair the assigned issues by asking the LLM for one atomic EditOp plan

    `context_issues` are lower-priority issues open on the same diagram. They
    are passed as background so the plan for `issues` does not walk straight
    into a known problem, not as work to do in this round.
    """
    active_config = config or ExperimentConfig()
    show_evidence = active_config.include_formal_evidence
    payload: dict[str, Any] = {
        "ir_format": str(active_config.ir_format),
        "diagram": diagram_payload(diagram, config),
        "issues": [
            issue_to_dict(issue, include_formal_evidence=show_evidence)
            for issue in issues
        ],
        "repair_mode": RepairMode.ATOMIC,
        "id_constraints": _diagram_id_constraints(diagram),
    }
    if context_issues:
        payload["other_open_issues"] = [
            issue_to_dict(issue, include_formal_evidence=show_evidence)
            for issue in context_issues
        ]
    response_schema = _atomic_ops_schema_for_diagram(diagram)
    system_prompt = render_prompt_template(_ATOMIC_REPAIR_PROMPT, config=config)
    feedback: str | None = None
    for attempt in range(1, ATOMIC_PLAN_ATTEMPTS + 1):
        attempt_payload = dict(payload)
        if feedback:
            attempt_payload["repair_feedback"] = feedback
        try:
            # Structured outputs constrain the reply to the EditOp schema, so no
            # fenced scraping or best-effort JSON repair is needed.
            parsed = await llm_client.complete_structured(
                prompt=json.dumps(attempt_payload),
                schema=response_schema,
                system=system_prompt,
                model=resolve_model(TaskType.REPAIR, config=config),
                provider=resolve_provider(TaskType.REPAIR, config=config),
                reasoning_effort=str(config.reasoning_effort)
                if config and config.reasoning_effort
                else None,
                **resolve_sampling(config),
            )
            ops_data = parsed.get("ops", parsed) if isinstance(parsed, dict) else parsed
            _validate_atomic_op_ids(ops_data, diagram)
            return list(atomic_edit_op_list_adapter.validate_python(ops_data))
        except (ValueError, KeyError, TypeError) as exc:
            if attempt == ATOMIC_PLAN_ATTEMPTS:
                raise
            feedback = _atomic_plan_feedback(exc)
    raise AssertionError("unreachable: plan correction loop either returns or raises")


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
    applied_op_origins: list[OpOrigin] = []
    failed_ops: list[EditOpResult] = []
    failed_op_origins: list[OpOrigin] = []
    iterations = 0
    # every diagram state the loop has already produced, so an edit that undoes
    # an earlier one is recognised as a cycle rather than run to the budget
    seen_states = {_state_fingerprint(current)}
    stop_reason = StopReason.ITERATION_BUDGET

    while iterations < active_config.max_repair_iters:
        assigned_issues = _highest_priority_batch(remaining)
        if not assigned_issues:
            stop_reason = StopReason.CONVERGED
            break

        if active_config.repair_mode == RepairMode.REGEN:
            result = await active_repair_fn(
                current,
                issues=assigned_issues,
                config=active_config,
                snapshot=False,
            )
            current = result.repaired_diagram
            applied_ops.append(ReplaceDiagramOp(diagram=current))
            applied_op_origins.append(OpOrigin.MODEL_REGEN)
        else:
            ops = _batch_quick_fixes(assigned_issues, current)
            origin = OpOrigin.QUICK_FIX
            if ops is None:
                origin = OpOrigin.MODEL_PLAN
                assigned_ids = {id(issue) for issue in assigned_issues}
                ops = await active_atomic_repair_fn(
                    current,
                    issues=assigned_issues,
                    config=active_config,
                    context_issues=[
                        other for other in remaining if id(other) not in assigned_ids
                    ],
                )
            current, op_results = apply_edit_ops(ops, current)
            for op_result in op_results:
                if op_result.applied:
                    applied_ops.append(op_result.op)
                    applied_op_origins.append(origin)
                else:
                    failed_ops.append(op_result)
                    failed_op_origins.append(origin)

        validation = await validate_diagram(
            current,
            include_semantic=active_config.tiers_enabled.t3,
            config=active_config,
        )
        remaining = validation.issues + validation.semantic_issues
        iterations += 1

        # An iteration that leaves the diagram exactly as it was cannot make the
        # next one turn out differently: the same issue is selected, the same
        # prompt is built, and the budget drains on a loop that has stalled. A
        # state seen earlier means the same thing one step removed — the edits
        # are cycling between states rather than converging on one.
        fingerprint = _state_fingerprint(current)
        if fingerprint in seen_states:
            stop_reason = (
                StopReason.NO_PROGRESS
                if fingerprint == _state_fingerprint(diagram) and iterations == 1
                else StopReason.REPEATED_STATE
            )
            break
        seen_states.add(fingerprint)

    if (
        stop_reason is StopReason.ITERATION_BUDGET
        and not _highest_priority_batch(remaining)
    ):
        # the budget ran out on the same iteration that cleared the last issue
        stop_reason = StopReason.CONVERGED

    return DispatcherRepairResult(
        repaired_diagram=current,
        applied_ops=applied_ops,
        applied_op_origins=applied_op_origins,
        failed_ops=failed_ops,
        failed_op_origins=failed_op_origins,
        remaining_issues=remaining,
        iterations=iterations,
        converged=not _highest_priority_batch(remaining),
        errors_resolved=_has_no_errors(remaining),
        stop_reason=stop_reason,
    )


def _state_fingerprint(diagram: BpmnDiagram) -> str:
    """a stable identity for one diagram state

    Layout is included: an edit that only moves a shape is still a change, and
    excluding geometry would let a repair that repositions elements read as a
    stalled iteration.
    """
    return hashlib.sha256(
        json.dumps(diagram.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    ).hexdigest()


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
    gateway_ids = constraints["gateway_ids"]
    process_ids = constraints["process_ids"]

    for def_name, field_name, enum_values in [
        ("RemoveNodeOp", "id", node_ids),
        ("RenameNodeOp", "id", node_ids),
        ("ChangeNodeTypeOp", "id", node_ids),
        ("ChangeGatewayTypeOp", "id", gateway_ids),
        ("RemoveFlowOp", "id", flow_ids),
        ("RenameFlowOp", "id", flow_ids),
        ("SetConditionOp", "flow_id", flow_ids),
        ("AddNodeOp", "process_id", process_ids),
    ]:
        _set_schema_enum(schema, def_name, field_name, enum_values)

    # Endpoints stay open because a plan may legitimately create a node and then
    # connect it. The ordered-plan validator enforces that such references exist
    # by the time each add_flow is reached.
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
        f"{field_name}. Prefer an existing node ID ({listed}). Only when the plan "
        "deliberately inserts a genuinely missing BPMN node may this reference the "
        "ID of an earlier add_node in the same plan."
    )


def _atomic_plan_feedback(error: Exception) -> str:
    return (
        "The previous operation plan was rejected before any operation was applied. "
        f"Reason: {error} "
        "Reassess the operation types, IDs, and relationships, then return one "
        "complete minimal replacement plan for every assigned issue. If a proposed "
        "add_node was rejected as disconnected, do not merely invent another node "
        "ID: use add_flow when the intended change is a connection between existing "
        "nodes, or connect the new node if it is genuinely required. Do not enumerate "
        "alternatives and do not explain the correction."
    )


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
        "gateway_ids": sorted(
            node.id
            for proc in diagram.processes
            for node in proc.flow_nodes
            if node.type in GATEWAY_NODE_TYPES
        ),
    }


def _validate_atomic_op_ids(ops_data: Any, diagram: BpmnDiagram) -> None:
    """reject ops referencing ids that will not exist when the op is applied

    Ops apply in order, so the set of valid ids is walked forward with them: a
    node introduced by an earlier `add_node` is a legal endpoint for a later
    `add_flow`, and one removed by an earlier `remove_node` is not. Pinning the
    check to the diagram as it arrived would make `add_node` useless — a new node
    could never be connected to anything, which is a defect in itself.

    The walk tracks three things beyond node and flow existence. Process IDs join
    the taken set, because BPMN scopes `id` over the whole document and a new
    flow named after its own process is a collision the old check waved through.
    Flow endpoints are simulated too, so a `remove_node` with `cascade` also
    retires the flows it takes with it — otherwise a later op could address a
    flow that no longer exists by the time it runs.
    """
    if not isinstance(ops_data, list):
        return

    constraints = _diagram_id_constraints(diagram)
    node_ids = set(constraints["node_ids"])
    flow_ids = set(constraints["flow_ids"])
    gateway_ids = set(constraints["gateway_ids"])
    process_ids = set(constraints["process_ids"])
    # every declared id, whatever kind — this is what "already exists" means
    taken = node_ids | flow_ids | process_ids
    endpoints = {
        flow.id: (flow.source_ref, flow.target_ref)
        for proc in diagram.processes
        for flow in proc.sequence_flows
    }
    introduced_node_ids: set[str] = set()

    for index, op_data in enumerate(ops_data):
        if not isinstance(op_data, dict):
            continue
        op = op_data.get("op")
        if op in {
            "remove_node",
            "rename_node",
            "change_node_type",
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
                _simulate_remove_node(
                    op_data, node_ids, flow_ids, taken, endpoints
                )
                gateway_ids.discard(op_data.get("id"))
        elif op == "change_gateway_type":
            _require_id_in_enum(
                op_data.get("id"),
                gateway_ids,
                "gateway",
                index,
                "id",
                sorted(gateway_ids),
            )
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
            _reject_taken_id(op_data.get("id"), taken, index, "add_flow")
            flow_id = op_data.get("id")
            _register_new_id(flow_id, flow_ids, taken)
            if isinstance(flow_id, str) and flow_id:
                endpoints[flow_id] = (
                    str(op_data.get("source_ref")),
                    str(op_data.get("target_ref")),
                )
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
                _retire_flow(op_data.get("id"), flow_ids, taken, endpoints)
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
            _reject_taken_id(op_data.get("id"), taken, index, "add_node")
            # the new node becomes a legal endpoint for later ops in this list
            node_id = op_data.get("id")
            _register_new_id(node_id, node_ids, taken)
            if isinstance(node_id, str) and node_id:
                introduced_node_ids.add(node_id)

    disconnected = sorted(
        node_id
        for node_id in introduced_node_ids
        if node_id in node_ids
        and not any(
            node_id == source or node_id == target
            for source, target in endpoints.values()
        )
    )
    if disconnected:
        preview = disconnected[:8]
        listed = ", ".join(preview)
        omitted = len(disconnected) - len(preview)
        suffix = f" (and {omitted} more)" if omitted else ""
        raise ValueError(
            "Invalid repair plan: add_node creates a BPMN node, not a sequence flow. "
            "Every new node must be connected by add_flow in the same plan. For a "
            "missing connection between existing nodes, use add_flow directly. "
            f"Disconnected new node(s): {listed}{suffix}."
        )


def _simulate_remove_node(
    op_data: dict[str, Any],
    node_ids: set[str],
    flow_ids: set[str],
    taken: set[str],
    endpoints: dict[str, tuple[str, str]],
) -> None:
    """advance the simulated state past a `remove_node`

    The node goes whatever `cascade` says: a plan that removes an element and
    then addresses it has contradicted itself, and catching that here is the
    whole point of walking the list in order. Whether `apply_edit_ops` would
    happen to roll the removal back does not make the plan coherent.

    `cascade` additionally retires the flows the removal takes with it, so a
    later op cannot address a flow that is gone by the time it runs.
    """
    node_id = op_data.get("id")
    if not isinstance(node_id, str):
        return
    if op_data.get("cascade"):
        incident = [
            flow_id
            for flow_id, (source, target) in endpoints.items()
            if node_id in (source, target)
        ]
        for flow_id in incident:
            _retire_flow(flow_id, flow_ids, taken, endpoints)
    node_ids.discard(node_id)
    taken.discard(node_id)


def _retire_flow(
    value: Any,
    flow_ids: set[str],
    taken: set[str],
    endpoints: dict[str, tuple[str, str]],
) -> None:
    if not isinstance(value, str):
        return
    flow_ids.discard(value)
    taken.discard(value)
    endpoints.pop(value, None)


def _reject_taken_id(value: Any, known: set[str], index: int, op_name: str) -> None:
    """refuse to introduce an id that already exists in the diagram"""
    if isinstance(value, str) and value in known:
        raise ValueError(
            f"op[{index}] {op_name} id '{value}' already exists — use remove_node "
            "or add_flow to act on an existing element."
        )


def _register_new_id(value: Any, known: set[str], taken: set[str]) -> None:
    """record an id an op introduces so later ops in the same list may use it"""
    if isinstance(value, str) and value:
        known.add(value)
        taken.add(value)


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


# Warnings that describe a failed check rather than a defect in the diagram. No
# edit operation can resolve them — an unparseable model reply or a checker that
# timed out is a fact about the run, not about the process — so they are neither
# selected for repair nor allowed to hold convergence open.
_NON_REPAIRABLE_RULE_IDS = frozenset({"LLM_PARSE_ERROR"})
_NON_REPAIRABLE_SUFFIXES = (":runtime_error",)


def _is_repairable(issue: ValidationIssue) -> bool:
    rule_id = str(issue.rule_id)
    if rule_id in _NON_REPAIRABLE_RULE_IDS:
        return False
    return not rule_id.endswith(_NON_REPAIRABLE_SUFFIXES)


def _highest_priority_batch(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    """issues assigned to the next plan: all errors first, then all warnings

    A batch gives the model one chance to produce a coherent repair plan for the
    current state. The dispatcher applies the plan and revalidates once; only
    findings that remain or appear after that validation cause another call.
    Errors and warnings stay in separate rounds because warnings may disappear
    as a consequence of repairing the errors.
    """
    repairable = [issue for issue in issues if _is_repairable(issue)]
    errors = [issue for issue in repairable if issue.severity == "error"]
    if errors:
        return errors
    return [issue for issue in repairable if issue.severity == "warning"]


def _batch_quick_fixes(
    issues: list[ValidationIssue],
    diagram: BpmnDiagram,
) -> list[EditOp] | None:
    """combine deterministic fixes only when every assigned issue has one"""
    combined: list[EditOp] = []
    for issue in issues:
        ops = propose_quick_fix(issue, diagram)
        if ops is None:
            return None
        combined.extend(ops)
    return combined


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
