"""Provider-agnostic E8 enhancement runner and scorer."""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping

from app.experiment_runner import _call_record
from app.experiments import (
    ExperimentConfig,
    app_commit,
    config_hash,
    converter_version,
    hash_bytes,
    prompt_version,
)
from app.llm.tracing import (
    get_traces,
    models_called,
    reset_trace_context,
    start_trace_context,
    trace_usage,
)
from app.model.formats.pydantic_ir import PydanticConverter
from app.model.schema import BpmnDiagram
from app.repair.ops import apply_edit_ops, edit_op_list_adapter
from app.services import chat as chat_service
from app.services.ir_payload import diagram_payload_text
from app.validation.rules import Severity, ValidationIssue, ValidationTier, validate


ChatFn = Callable[..., Awaitable[Any]]
DEFAULT_E8_CONCURRENCY = 4


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
    core: BpmnDiagram | None = None,
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
        label = args[0].get("label") if isinstance(args[0], Mapping) else None
        if not isinstance(label, str):
            return None, "manual_review"
        matches = _node_candidates(nodes, args[0])
        return (True, "exists_task") if matches else (False, "missing_task")
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
        gateway_args = _arguments(args, "condition_gateway")
        target_arg = _argument(args, "branch_target")
        flow_arg = _argument(args, "branch_flow")
        if not gateway_args or target_arg is None or flow_arg is None:
            return None, "manual_review"
        gateways = set().union(*(_node_candidates(nodes, item) for item in gateway_args))
        targets = _node_candidates(nodes, target_arg)
        branch_label = flow_arg.get("label")
        matching_flows = [
            flow
            for flow in flows.values()
            if flow.source_ref in gateways
            and flow.target_ref in targets
            and (not isinstance(branch_label, str) or _labels_equivalent(flow.name, branch_label))
        ]
        if not matching_flows:
            return False, "branch_not_restored"
        if reference is None:
            return None, "manual_review"
        flow = matching_flows[0]
        flow_id = str(flow_arg.get("element_ref", ""))
        reference_flows = {
            item.id: item
            for process in reference.processes
            for item in process.sequence_flows
        }
        reference_flow = reference_flows.get(flow_id)
        if reference_flow is None:
            return None, "manual_review"
        if flow.condition_expression != reference_flow.condition_expression and core is not None:
            core_flows = {
                item.id: item
                for process in core.processes
                for item in process.sequence_flows
            }
            core_flow = core_flows.get(flow_id)
            if core_flow is not None and flow.condition_expression == core_flow.condition_expression:
                return False, "branch_condition_unchanged"
        return True, "branch_condition"
    if relation_type == "exists_path":
        path = [
            _node_candidates(nodes, item)
            for item in args
            if isinstance(item, Mapping) and item.get("role", "").startswith("path_")
        ]
        if len(path) < 2:
            return None, "manual_review"
        if any(not item for item in path):
            return False, "path_element_missing"
        return (
            (True, "exists_path")
            if all(
                any(_reachable(graph, left, right) for left in sources for right in targets)
                for sources, targets in zip(path, path[1:])
            )
            else (False, "path_not_restored")
        )
    if relation_type == "reaches":
        outcome_arg = _argument(args, "outcome")
        starts = [node.id for node in nodes.values() if node.type.value == "startEvent"]
        if outcome_arg is None or not starts:
            return None, "manual_review"
        targets = _node_candidates(nodes, outcome_arg)
        if not targets:
            return False, "outcome_missing"
        return (
            (True, "reaches")
            if any(_reachable(graph, start, target) for start in starts for target in targets)
            else (False, "outcome_unreachable")
        )
    if relation_type == "additional_action":
        context = _argument_id(args, "context_flow")
        template_arg = _argument(args, "action_template")
        if context is None or template_arg is None or reference is None:
            return None, "manual_review"
        result_matches = _node_candidates(nodes, template_arg, allow_id=False)
        reference_nodes = {
            node.id: node
            for process in reference.processes
            for node in process.flow_nodes
        }
        reference_matches = _node_candidates(reference_nodes, template_arg, allow_id=False)
        return (
            (True, "policy_removal")
            if 0 < len(result_matches) <= len(reference_matches)
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
    if target is not None and config.tiers_enabled.t2 and tier2_runner is None:
        from app.validation.checkers import run_woflan

        tier2_runner = run_woflan
    if target is not None and tier2_runner is not None:
        try:
            value = tier2_runner(target)
            if asyncio.iscoroutine(value):
                value = await value
            if isinstance(value, list) and all(isinstance(item, ValidationIssue) for item in value):
                unsupported = any(item.rule_id == "woflan:unsupported" for item in value)
                errors = [item for item in value if item.severity == Severity.ERROR]
                tier2_valid = None if unsupported else not errors
                tier2_status = "unsupported" if unsupported else ("pass" if tier2_valid else "fail")
            else:
                tier2_valid = bool(value if isinstance(value, bool) else getattr(value, "is_valid", value))
                tier2_status = "pass" if tier2_valid else "fail"
        except TimeoutError:
            tier2_status = "timeout"
        except Exception:
            tier2_status = "error"
    requirement_value, requirement_status = _requirement(case, target, reference, core)
    manual_review = _manual_status(requirement_status)
    preservation_rate, unnecessary = (
        _preservation(case, core, reference, target)
        if target is not None
        else (None, 0)
    )
    diagram_returned = target is not None
    full_success = diagram_returned and tier1_valid is True and tier2_valid is not False and requirement_value is True and manual_review == "not_required"
    core_path = _path(root, str(case["core"]))
    reference_path = _path(root, str(case["reference"]))
    instruction = str(case["instruction"])
    relation = case.get("relation")
    relation_type = str(relation.get("type")) if isinstance(relation, Mapping) else None
    case_id = str(case.get("case_id"))
    return {
        "case_id": case_id,
        "seed_id": case.get("seed_id"),
        "operator": case_id.split("-", 1)[0],
        "relation_type": relation_type,
        "core_path": str(core_path),
        "core_hash": hash_bytes(core_path.read_bytes()),
        "reference_path": str(reference_path),
        "reference_hash": hash_bytes(reference_path.read_bytes()),
        "instruction_hash": hash_bytes(instruction.encode("utf-8")),
        "instruction_chars": len(instruction),
        "config": config.model_dump(mode="json"),
        "config_hash": config_hash(config),
        "app_commit": app_commit(),
        "converter_version": converter_version(config),
        "prompt_version": prompt_version(chat_service.chat_prompt_path()).model_dump(mode="json"),
        "model_used": models_called(traces),
        "calls": [_call_record(trace, False).model_dump(mode="json") for trace in traces],
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
    concurrency: int = DEFAULT_E8_CONCURRENCY,
    selected_case_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    if selected_case_ids is not None:
        selected = [str(case_id) for case_id in selected_case_ids]
        if len(selected) != len(set(selected)):
            raise ValueError("selected E8 case IDs must be unique")
        available = {str(case.get("case_id")): case for case in cases}
        missing = sorted(set(selected) - set(available))
        if missing:
            raise ValueError(f"unknown E8 case IDs: {', '.join(missing)}")
        cases = [available[case_id] for case_id in selected]
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
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    semaphore = asyncio.Semaphore(concurrency)

    async def execute(case: Mapping[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await run_case(case, root=root, config=config, mock=mock)

    with out_path.open("a" if resume else "w", encoding="utf-8") as handle:
        tasks = [asyncio.create_task(execute(case)) for case in pending]
        for task in asyncio.as_completed(tasks):
            record = await task
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
    return {
        "planned": len(cases),
        "executed": len(pending),
        "skipped": len(cases) - len(pending),
        "concurrency": concurrency,
        "results": str(out_path),
    }


def analyze_e8(path: Path) -> dict[str, Any]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    def rate(key: str) -> float | None:
        values = [item[key] for item in records if isinstance(item.get(key), bool)]
        return sum(values) / len(values) if values else None
    result = {
        "records": len(records),
        "errors": sum(bool(item.get("error")) for item in records),
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
        "usage": {
            key: sum(item.get("usage", {}).get(key, 0) for item in records)
            for key in (
                "calls",
                "calls_missing_usage",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "cached_input_tokens",
                "reasoning_tokens",
            )
        },
    }
    result["by_operator"] = _group_analysis(records, "operator")
    result["by_relation"] = _group_analysis(records, "relation_type")
    review_path = path.with_name("manual-review.json")
    if review_path.is_file():
        result["manual_review"] = _manual_review_summary(review_path, len(records))
    return result


def _manual_review_summary(path: Path, record_count: int) -> dict[str, Any]:
    """Return the reviewed E8 measures kept separate from automatic scoring."""
    review = json.loads(path.read_text(encoding="utf-8"))
    cases = review.get("cases")
    if not isinstance(cases, list):
        raise ValueError(f"invalid E8 manual review: {path}")

    numeric_fields = (
        "instruction_level_alternative_valid_count",
        "instruction_level_successes_after_review",
        "safety_qualified_alternative_valid_count",
        "safety_qualified_successes_after_review",
        "rejected_count",
        "formal_verdict_inconclusive_count",
    )
    values: dict[str, int] = {}
    for field in numeric_fields:
        value = review.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"invalid E8 manual-review field {field!r}: {path}")
        values[field] = value

    instruction_successes = values["instruction_level_successes_after_review"]
    safety_successes = values["safety_qualified_successes_after_review"]
    return {
        "status": review.get("status"),
        "reviewed_cases": len(cases),
        "review_type": review.get("review_type"),
        "human_confirmed": review.get("human_confirmed") is True,
        **values,
        "instruction_level_success_rate_after_review": (
            instruction_successes / record_count if record_count else None
        ),
        "safety_qualified_success_rate_after_review": (
            safety_successes / record_count if record_count else None
        ),
    }


def _group_analysis(records: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in records:
        groups.setdefault(str(item.get(key, "unknown")), []).append(item)
    return {
        name: {
            "records": len(items),
            "requirement_satisfaction_rate": _bool_rate(items, "requirement_satisfied"),
            "full_enhancement_success_rate": _bool_rate(items, "full_enhancement_success"),
            "mean_preservation_rate": _mean(item.get("preservation_rate") for item in items),
        }
        for name, items in sorted(groups.items())
    }


def _bool_rate(records: list[dict[str, Any]], key: str) -> float | None:
    values = [item[key] for item in records if isinstance(item.get(key), bool)]
    return sum(values) / len(values) if values else None


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


def _argument(args: list[Any], role: str) -> Mapping[str, Any] | None:
    for item in args:
        if isinstance(item, Mapping) and item.get("role") == role:
            return item
    return None


def _arguments(args: list[Any], role: str) -> list[Mapping[str, Any]]:
    return [
        item
        for item in args
        if isinstance(item, Mapping) and item.get("role") == role
    ]


_LABEL_STOPWORDS = {"a", "an", "the", "to", "of"}


def _label_tokens(value: str | None) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (value or "").casefold())
        if token not in _LABEL_STOPWORDS
    }


def _labels_equivalent(actual: str | None, expected: str | None) -> bool:
    actual_tokens = _label_tokens(actual)
    expected_tokens = _label_tokens(expected)
    if not actual_tokens or not expected_tokens:
        return False
    # Added qualifiers (for example "customer" in "send customer updates")
    # do not make a semantically requested action incorrect.
    return actual_tokens <= expected_tokens or expected_tokens <= actual_tokens


def _node_candidates(
    nodes: Mapping[str, Any],
    argument: Mapping[str, Any],
    *,
    allow_id: bool = True,
) -> set[str]:
    expected_id = str(argument.get("element_ref", ""))
    expected_label = argument.get("label")
    matches = {
        node_id
        for node_id, node in nodes.items()
        if isinstance(expected_label, str)
        and _labels_equivalent(getattr(node, "name", None), expected_label)
    }
    # IDs are stable only for unchanged elements. An inserted node may use any
    # valid ID, and an ID collision must not override a contradictory label.
    if allow_id and expected_id in nodes:
        node = nodes[expected_id]
        if not isinstance(expected_label, str) or _labels_equivalent(
            getattr(node, "name", None), expected_label
        ):
            matches.add(expected_id)
    return matches


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
