"""Provider-agnostic E8 enhancement runner and scorer."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping

from app.experiments import ExperimentConfig, config_hash
from app.llm.tracing import get_traces, reset_trace_context, start_trace_context, trace_usage
from app.model.formats.pydantic_ir import PydanticConverter
from app.model.schema import BpmnDiagram
from app.repair.ops import apply_edit_ops, edit_op_list_adapter
from app.services import chat as chat_service
from app.services.ir_payload import diagram_payload_text
from app.validation.rules import ValidationTier, validate


ChatFn = Callable[..., Awaitable[Any]]


def load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases", []) if isinstance(payload, Mapping) else payload
    if not isinstance(cases, list):
        raise ValueError("E8 cases must be a JSON list or an object with cases")
    return [dict(case) for case in cases]


def _path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _diagram(path: Path) -> BpmnDiagram:
    diagram, unsupported = PydanticConverter().parse_with_diagnostics(path.read_bytes())
    if unsupported:
        raise ValueError(f"unsupported elements in {path}")
    return diagram


def _projection(diagram: BpmnDiagram) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for process in diagram.processes:
        for node in process.flow_nodes:
            value = node.model_dump(mode="json")
            value.pop("bounds", None)
            value.pop("label_bounds", None)
            value["incoming"] = sorted(value.get("incoming", []))
            value["outgoing"] = sorted(value.get("outgoing", []))
            values[node.id] = {"kind": "node", "value": value}
        for flow in process.sequence_flows:
            value = flow.model_dump(mode="json")
            value.pop("waypoints", None)
            value.pop("label_bounds", None)
            values[flow.id] = {"kind": "flow", "value": value}
    return values


def _structural_distance(left: BpmnDiagram, right: BpmnDiagram) -> int:
    a, b = _projection(left), _projection(right)
    return sum(a.get(key) != b.get(key) for key in set(a) | set(b))


def _preservation(
    case: Mapping[str, Any],
    core: BpmnDiagram,
    reference: BpmnDiagram,
    result: BpmnDiagram,
) -> tuple[float | None, int]:
    ids = [str(item) for item in case.get("preserved_element_ids", [])]
    if not ids:
        return None, 0
    before = _projection(core)
    expected = _projection(reference)
    after = _projection(result)
    preserved = 0
    for item in ids:
        # A required insertion can change the incoming or outgoing references
        # of an existing neighbour.  In that case the reference, not M_core,
        # defines the intended state.  Unaffected elements must stay equal to
        # M_core because their core and reference projections are identical.
        intended = expected.get(item, before.get(item))
        preserved += item in after and after.get(item) == intended
    return preserved / len(ids), len(ids) - preserved


def _requirement(
    case: Mapping[str, Any],
    result: BpmnDiagram | None,
    reference: BpmnDiagram | None = None,
) -> tuple[bool | None, str]:
    if result is None:
        return False, "diagram_missing"
    relation = case.get("relation")
    if not isinstance(relation, Mapping):
        return None, "manual_review"
    args = relation.get("arguments")
    if not isinstance(args, list) or not args:
        return None, "manual_review"
    relation_type = str(relation.get("type", ""))
    nodes = {node.id: node for process in result.processes for node in process.flow_nodes}
    flows = {flow.id: flow for process in result.processes for flow in process.sequence_flows}
    graph = _graph(result)
    if relation_type == "exists_task":
        ids = [str(item) for item in case.get("expected_element_ids", [])]
        if len(ids) != 1:
            return None, "manual_review"
        target = nodes.get(ids[0])
        label = args[0].get("label") if isinstance(args[0], Mapping) else None
        if target is None or not target.name:
            return False, "missing_task"
        if not isinstance(label, str):
            return None, "manual_review"
        return (
            (True, "exists_task")
            if target.name.casefold() == label.casefold()
            else (False, "task_label_mismatch")
        )
    if relation_type == "precedes":
        before = _argument_id(args, "before")
        after = _argument_id(args, "after")
        if before is None or after is None:
            return None, "manual_review"
        return (
            (True, "precedes")
            if before in nodes and after in nodes and _reachable(graph, before, after)
            else (False, "ordering_not_restored")
        )
    if relation_type == "branch_condition":
        gateways = _argument_ids(args, "condition_gateway")
        target = _argument_id(args, "branch_target")
        flow_id = _argument_id(args, "branch_flow")
        if not gateways or target is None or flow_id is None:
            return None, "manual_review"
        flow = flows.get(flow_id)
        branch_label = _argument_label(args, "branch_flow")
        if flow is None or flow.source_ref not in gateways or flow.target_ref != target:
            return False, "branch_not_restored"
        if branch_label is not None and (flow.name or "").casefold() != branch_label.casefold():
            return False, "branch_label_mismatch"
        if reference is None:
            return None, "manual_review"
        reference_flows = {
            item.id: item
            for process in reference.processes
            for item in process.sequence_flows
        }
        reference_flow = reference_flows.get(flow_id)
        if reference_flow is None:
            return None, "manual_review"
        if flow.condition_expression != reference_flow.condition_expression:
            return False, "branch_condition_mismatch"
        return True, "branch_condition"
    if relation_type == "exists_path":
        path = [
            str(item.get("element_ref"))
            for item in args
            if isinstance(item, Mapping) and item.get("role", "").startswith("path_")
        ]
        if len(path) < 2:
            return None, "manual_review"
        if any(item not in nodes for item in path):
            return False, "path_element_missing"
        return (
            (True, "exists_path")
            if all(_reachable(graph, left, right) for left, right in zip(path, path[1:]))
            else (False, "path_not_restored")
        )
    if relation_type == "reaches":
        target = _argument_id(args, "outcome")
        starts = [node.id for node in nodes.values() if node.type.value == "startEvent"]
        if target is None or not starts:
            return None, "manual_review"
        if target not in nodes:
            return False, "outcome_missing"
        return (
            (True, "reaches")
            if any(_reachable(graph, start, target) for start in starts)
            else (False, "outcome_unreachable")
        )
    if relation_type == "additional_action":
        added = _ids(case, "added_element_ids", "added_ids")
        context = _argument_id(args, "context_flow")
        template = _argument_id(args, "action_template")
        if not added or context is None or template is None:
            return None, "manual_review"
        return (
            (True, "policy_removal")
            if not (added & set(nodes) | added & set(flows))
            and context in flows
            and template in nodes
            else (False, "unwanted_action_remains")
        )
    return None, "manual_review"


def _tier1(result: BpmnDiagram | None) -> tuple[bool | None, list[str]]:
    if result is None:
        return None, []
    issues = [issue for issue in validate(result).issues if issue.tier == ValidationTier.TIER1]
    return not issues, sorted({issue.rule_id for issue in issues})


def _manual_status(requirement_status: str) -> str:
    """Require review only when the returned relation cannot be scored in code."""
    return "required" if requirement_status == "manual_review" else "not_required"


async def run_case(
    case: Mapping[str, Any],
    *,
    root: Path,
    config: ExperimentConfig,
    chat_fn: ChatFn | None = None,
    tier2_runner: Callable[[BpmnDiagram], Any] | None = None,
    mock: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    core = _diagram(_path(root, str(case["core"])))
    reference = _diagram(_path(root, str(case["reference"])))
    target: BpmnDiagram | None = None
    error: str | None = None
    if chat_fn is None:
        chat_fn = chat_service.chat_diagram
    token = start_trace_context()
    try:
        if mock:
            plan = edit_op_list_adapter.validate_python(case.get("oracle_plan", []))
            target, results = apply_edit_ops(plan, core)
            if not all(item.applied for item in results):
                raise ValueError("mock oracle plan did not apply")
            original = chat_service.llm_client.complete_structured_with_history

            async def fake_complete_structured_with_history(**_: Any) -> dict[str, Any]:
                return {"description": "Mock refinement.", "result": {"diagram": diagram_payload_text(target, config)}}

            chat_service.llm_client.complete_structured_with_history = fake_complete_structured_with_history
            try:
                response = await chat_fn(
                    messages=[chat_service.ChatMessage(role="user", content=str(case["instruction"]))],
                    diagram=core,
                    config=config,
                    snapshot_changes=False,
                )
            finally:
                chat_service.llm_client.complete_structured_with_history = original
        else:
            response = await chat_fn(
                messages=[chat_service.ChatMessage(role="user", content=str(case["instruction"]))],
                diagram=core,
                config=config,
                snapshot_changes=False,
            )
        target = response.updated_diagram
    except Exception as exc:  # record one case failure and continue
        error = f"{type(exc).__name__}: {exc}"
    finally:
        traces = get_traces()
        usage = trace_usage(traces).model_dump(mode="json")
        reset_trace_context(token)

    tier1_valid, tier1_issues = _tier1(target)
    tier2_valid: bool | None = None
    tier2_status = "not_run"
    if target is not None and tier2_runner is not None:
        try:
            value = tier2_runner(target)
            if asyncio.iscoroutine(value):
                value = await value
            tier2_valid = bool(value if isinstance(value, bool) else getattr(value, "is_valid", value))
            tier2_status = "pass" if tier2_valid else "fail"
        except TimeoutError:
            tier2_status = "timeout"
        except Exception:
            tier2_status = "error"
    requirement_value, requirement_status = _requirement(case, target, reference)
    manual_review = _manual_status(requirement_status)
    preservation_rate, unnecessary = (
        _preservation(case, core, reference, target)
        if target is not None
        else (None, 0)
    )
    diagram_returned = target is not None
    full_success = diagram_returned and tier1_valid is True and tier2_valid is not False and requirement_value is True and manual_review == "not_required"
    return {
        "case_id": case.get("case_id"),
        "seed_id": case.get("seed_id"),
        "config": config.model_dump(mode="json"),
        "config_hash": config_hash(config),
        "diagram_returned": diagram_returned,
        "tier1_valid": tier1_valid,
        "tier1_issue_ids": tier1_issues,
        "tier2_valid": tier2_valid,
        "tier2_status": tier2_status,
        "requirement_satisfied": requirement_value,
        "requirement_status": requirement_status,
        "preservation_rate": preservation_rate,
        "unnecessary_changes": unnecessary,
        "ged_to_reference": _structural_distance(target, reference) if target is not None else None,
        "manual_review": manual_review,
        "full_enhancement_success": full_success,
        "latency_ms": max(0, round((time.perf_counter() - started) * 1000)),
        "usage": usage,
        "error": error,
        "final_diagram": target.model_dump(mode="json") if target is not None else None,
    }


async def run_e8(
    cases_path: Path,
    out_path: Path,
    *,
    root: Path | None = None,
    config: ExperimentConfig | None = None,
    mock: bool = False,
    resume: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    root = root or cases_path.parent
    config = config or ExperimentConfig()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if resume and out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                done.add(str(json.loads(line).get("case_id")))
            except json.JSONDecodeError:
                continue
    pending = [case for case in cases if str(case.get("case_id")) not in done]
    if limit is not None:
        pending = pending[:limit]
    with out_path.open("a" if resume else "w", encoding="utf-8") as handle:
        for case in pending:
            record = await run_case(case, root=root, config=config, mock=mock)
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
    return {"planned": len(cases), "executed": len(pending), "skipped": len(cases) - len(pending), "results": str(out_path)}


def analyze_e8(path: Path) -> dict[str, Any]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    def rate(key: str) -> float | None:
        values = [item[key] for item in records if isinstance(item.get(key), bool)]
        return sum(values) / len(values) if values else None
    return {
        "records": len(records),
        "diagram_returned_rate": rate("diagram_returned"),
        "tier1_valid_rate": rate("tier1_valid"),
        "tier2_valid_rate": rate("tier2_valid"),
        "requirement_satisfaction_rate": rate("requirement_satisfied"),
        "full_enhancement_success_rate": rate("full_enhancement_success"),
        "manual_review_count": sum(item.get("manual_review") == "required" for item in records),
        "tier2_timeouts": sum(item.get("tier2_status") == "timeout" for item in records),
        "mean_preservation_rate": _mean(item.get("preservation_rate") for item in records),
        "mean_unnecessary_changes": _mean(item.get("unnecessary_changes") for item in records),
        "mean_ged_to_reference": _mean(item.get("ged_to_reference") for item in records),
        "mean_latency_ms": _mean(item.get("latency_ms") for item in records),
        "usage": {"calls": sum(item.get("usage", {}).get("calls", 0) for item in records), "calls_missing_usage": sum(item.get("usage", {}).get("calls_missing_usage", 0) for item in records)},
    }


def _mean(values: Iterable[Any]) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return sum(numbers) / len(numbers) if numbers else None


def _graph(diagram: BpmnDiagram) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {
        node.id: set()
        for process in diagram.processes
        for node in process.flow_nodes
    }
    for process in diagram.processes:
        for flow in process.sequence_flows:
            if flow.source_ref in graph and flow.target_ref in graph:
                graph[flow.source_ref].add(flow.target_ref)
    return graph


def _reachable(graph: Mapping[str, set[str]], source: str, target: str) -> bool:
    pending = [source]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        pending.extend(graph.get(current, set()) - seen)
    return False


def _argument_id(args: list[Any], role: str) -> str | None:
    for item in args:
        if isinstance(item, Mapping) and item.get("role") == role:
            value = item.get("element_ref")
            return str(value) if value else None
    return None


def _argument_ids(args: list[Any], role: str) -> set[str]:
    return {
        str(item.get("element_ref"))
        for item in args
        if isinstance(item, Mapping)
        and item.get("role") == role
        and item.get("element_ref")
    }


def _argument_label(args: list[Any], role: str) -> str | None:
    for item in args:
        if isinstance(item, Mapping) and item.get("role") == role:
            value = item.get("label")
            return str(value) if isinstance(value, str) else None
    return None


def _ids(case: Mapping[str, Any], *names: str) -> set[str]:
    values: Any = None
    metadata = case.get("metadata")
    for name in names:
        if name in case:
            values = case[name]
            break
        if isinstance(metadata, Mapping) and name in metadata:
            values = metadata[name]
            break
    if isinstance(values, str):
        return {values}
    if isinstance(values, Iterable):
        return {str(value) for value in values if str(value)}
    return set()
