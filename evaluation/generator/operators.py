"""Deterministic defect operators for generated evaluation datasets."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from itertools import combinations
import random

from app.model.schema import BpmnDiagram, FlowNode, FlowNodeType, SequenceFlow
from app.repair.ops import (
    AddFlowOp,
    AddNodeOp,
    AtomicEditOp,
    ChangeGatewayTypeOp,
    RemoveFlowOp,
    RemoveNodeOp,
    apply_edit_ops,
)
from app.validation.rules import validate
from evaluation.generator.models import (
    DatasetInjectionOp,
    DefectClass,
    OperatorId,
    RepointFlowOp,
)


@dataclass(frozen=True)
class Injection:
    operator: OperatorId
    defect_class: DefectClass
    site: tuple[str, ...]
    expected_finding: str
    expected_elements: list[str]
    injection: list[DatasetInjectionOp]
    repair: list[AtomicEditOp]
    diagram: BpmnDiagram


@dataclass(frozen=True)
class CompositeInjection:
    defects: tuple[Injection, Injection]
    defect_class: DefectClass
    injection: list[DatasetInjectionOp]
    repair: list[AtomicEditOp]
    diagram: BpmnDiagram


def structural_injections(
    diagram: BpmnDiagram,
    *,
    seed_id: str,
    random_seed: int,
) -> list[Injection]:
    """Return S01-S03 injections, with one reproducible S03 site per seed."""
    candidates = structural_candidate_injections(diagram)
    injections = [
        item for item in candidates
        if item.operator is not OperatorId.DANGLING_FLOW_REF
    ]
    dangling_candidates = [
        item for item in candidates
        if item.operator is OperatorId.DANGLING_FLOW_REF
    ]
    if dangling_candidates:
        material = f"{random_seed}:{seed_id}:STRUCT:S03".encode()
        local_seed = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
        injections.append(random.Random(local_seed).choice(dangling_candidates))
    return sorted(injections, key=lambda item: (item.operator, item.site))


def structural_candidate_injections(diagram: BpmnDiagram) -> list[Injection]:
    """Return every independently applicable S01-S03 injection."""
    injections: list[Injection] = []
    for process in diagram.processes:
        starts = [
            node
            for node in process.flow_nodes
            if node.type is FlowNodeType.START_EVENT
        ]
        ends = [
            node
            for node in process.flow_nodes
            if node.type is FlowNodeType.END_EVENT
        ]

        if len(starts) == 1 and len(ends) >= 1:
            candidate = _remove_event(
                diagram,
                process.id,
                starts[0],
                OperatorId.DELETE_ONLY_START,
                "R001",
            )
            if candidate is not None:
                injections.append(candidate)

        if len(ends) == 1 and len(starts) >= 1:
            candidate = _remove_event(
                diagram,
                process.id,
                ends[0],
                OperatorId.DELETE_ONLY_END,
                "R002",
            )
            if candidate is not None:
                injections.append(candidate)
    injections.extend(_dangling_flow_injections(diagram))
    return sorted(injections, key=lambda item: (item.operator, item.site))


def disjoint_injection(
    diagram: BpmnDiagram,
    candidates: list[Injection],
    *,
    seed_id: str,
    random_seed: int,
) -> CompositeInjection | None:
    """Choose at most one reproducible pair with non-overlapping neighborhoods."""
    return _paired_injection(
        diagram,
        candidates,
        seed_id=seed_id,
        random_seed=random_seed,
        require_overlap=False,
        regime="DISJOINT",
    )


def interacting_injection(
    diagram: BpmnDiagram,
    candidates: list[Injection],
    *,
    seed_id: str,
    random_seed: int,
) -> CompositeInjection | None:
    """Choose one reproducible pair whose local footprints overlap."""
    return _paired_injection(
        diagram,
        candidates,
        seed_id=seed_id,
        random_seed=random_seed,
        require_overlap=True,
        regime="INTERACTING",
    )


def _paired_injection(
    diagram: BpmnDiagram,
    candidates: list[Injection],
    *,
    seed_id: str,
    random_seed: int,
    require_overlap: bool,
    regime: str,
) -> CompositeInjection | None:
    grouped: dict[tuple[DefectClass, OperatorId, OperatorId], list[tuple[Injection, Injection]]] = {}
    footprints = {
        id(injection): _injection_footprint(diagram, injection)
        for injection in candidates
    }
    for first, second in combinations(candidates, 2):
        if first.defect_class is not second.defect_class:
            continue
        overlaps = bool(footprints[id(first)] & footprints[id(second)])
        if overlaps is not require_overlap:
            continue
        operators = tuple(sorted((first.operator, second.operator)))
        grouped.setdefault(
            (first.defect_class, operators[0], operators[1]), []
        ).append((first, second))
    if not grouped:
        return None

    material = f"{random_seed}:{seed_id}:K2:{regime}".encode()
    local_seed = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    rng = random.Random(local_seed)
    group_keys = sorted(grouped, key=lambda item: tuple(str(part) for part in item))
    rng.shuffle(group_keys)
    for key in group_keys:
        pairs = sorted(
            grouped[key],
            key=lambda pair: tuple(
                (item.operator, item.site) for item in pair
            ),
        )
        rng.shuffle(pairs)
        for pair in pairs:
            ops = [op for defect in pair for op in defect.injection]
            updated, applied = apply_injection_ops(ops, diagram)
            if not all(applied):
                continue
            issues = validate(updated).issues
            rule_defects = [
                defect for defect in pair
                if defect.expected_finding.startswith("R")
            ]
            if not all(
                _expected_issue_present(defect, issues)
                for defect in rule_defects
            ):
                continue
            if not all(_injection_persists(defect, updated) for defect in pair):
                continue
            repair = [
                op
                for defect in reversed(pair)
                for op in defect.repair
            ]
            repaired, repair_results = apply_edit_ops(repair, updated)
            if (
                not all(result.applied for result in repair_results)
                or _without_layout(repaired) != _without_layout(diagram)
            ):
                continue
            return CompositeInjection(
                defects=pair,
                defect_class=pair[0].defect_class,
                injection=ops,
                repair=repair,
                diagram=updated,
            )
    return None


def apply_injection_ops(
    ops: list[DatasetInjectionOp], diagram: BpmnDiagram
) -> tuple[BpmnDiagram, list[bool]]:
    """Apply recorded dataset mutations, including deliberately invalid ones."""
    updated = diagram.model_copy(deep=True)
    applied: list[bool] = []
    for op in ops:
        if isinstance(op, RepointFlowOp):
            found = _find_flow(updated, op.flow_id)
            if found is None:
                applied.append(False)
                continue
            setattr(found[1], op.endpoint, op.new_ref)
            updated = BpmnDiagram.model_validate(updated.model_dump())
            applied.append(True)
            continue
        updated, results = apply_edit_ops([op], updated)
        applied.append(results[0].applied)
    return updated, applied


def _dangling_flow_injections(diagram: BpmnDiagram) -> list[Injection]:
    injections: list[Injection] = []
    declared_ids = set(diagram.element_ids())
    for process in diagram.processes:
        for flow in sorted(process.sequence_flows, key=lambda item: item.id):
            for endpoint, finding in (
                ("source_ref", "R005"),
                ("target_ref", "R006"),
            ):
                original_ref = getattr(flow, endpoint)
                missing_ref = f"Missing_{flow.id}_{endpoint}"
                if missing_ref in declared_ids:
                    continue
                mutation = RepointFlowOp(
                    flow_id=flow.id,
                    endpoint=endpoint,
                    new_ref=missing_ref,
                )
                updated, results = apply_injection_ops([mutation], diagram)
                findings = {issue.rule_id for issue in validate(updated).issues}
                if not all(results) or finding not in findings:
                    continue
                injections.append(
                    Injection(
                        operator=OperatorId.DANGLING_FLOW_REF,
                        defect_class=DefectClass.STRUCT,
                        site=(flow.id, endpoint, missing_ref),
                        expected_finding=finding,
                        expected_elements=[flow.id],
                        injection=[mutation],
                        repair=[
                            RemoveFlowOp(id=flow.id),
                            AddFlowOp(
                                process_id=process.id,
                                id=flow.id,
                                source_ref=(
                                    original_ref
                                    if endpoint == "source_ref"
                                    else flow.source_ref
                                ),
                                target_ref=(
                                    original_ref
                                    if endpoint == "target_ref"
                                    else flow.target_ref
                                ),
                                name=flow.name,
                                condition_expression=flow.condition_expression,
                            ),
                        ],
                        diagram=updated,
                    )
                )
    return injections


@dataclass(frozen=True)
class GatewayPair:
    process_id: str
    split_id: str
    join_id: str
    operator: OperatorId


def soundness_sites(diagram: BpmnDiagram) -> list[GatewayPair]:
    """Find structured XOR/XOR and AND/AND split-join regions."""
    pairs: list[GatewayPair] = []
    for process in diagram.processes:
        nodes = {node.id: node for node in process.flow_nodes}
        successors = {node_id: [] for node_id in nodes}
        predecessors = {node_id: [] for node_id in nodes}
        for flow in process.sequence_flows:
            if flow.source_ref in nodes and flow.target_ref in nodes:
                successors[flow.source_ref].append(flow.target_ref)
                predecessors[flow.target_ref].append(flow.source_ref)
        postdominators = _postdominators(nodes, successors)

        for split in sorted(process.flow_nodes, key=lambda node: node.id):
            if split.type not in {
                FlowNodeType.EXCLUSIVE_GATEWAY,
                FlowNodeType.PARALLEL_GATEWAY,
            } or len(successors[split.id]) < 2:
                continue
            candidates = []
            for join in process.flow_nodes:
                if (
                    join.id == split.id
                    or join.type is not split.type
                    or len(predecessors[join.id]) < 2
                    or not all(
                        join.id in postdominators[branch]
                        for branch in successors[split.id]
                    )
                ):
                    continue
                branch_inputs = [
                    set(predecessors[join.id])
                    & _reachable_before(branch, join.id, successors)
                    for branch in successors[split.id]
                ]
                if (
                    any(len(inputs) != 1 for inputs in branch_inputs)
                    or len({next(iter(inputs)) for inputs in branch_inputs})
                    != len(branch_inputs)
                    or set().union(*branch_inputs) != set(predecessors[join.id])
                ):
                    continue
                distances = [
                    _shortest_distance(branch, join.id, successors)
                    for branch in successors[split.id]
                ]
                if any(distance is None for distance in distances):
                    continue
                candidates.append((max(distances), sum(distances), join.id))

            if not candidates:
                continue
            candidates.sort()
            best_score = candidates[0][:2]
            closest = [item for item in candidates if item[:2] == best_score]
            if len(closest) != 1:
                continue
            join_id = closest[0][2]
            operator = (
                OperatorId.XOR_SPLIT_AND_JOIN
                if split.type is FlowNodeType.EXCLUSIVE_GATEWAY
                else OperatorId.AND_SPLIT_XOR_JOIN
            )
            pairs.append(
                GatewayPair(
                    process_id=process.id,
                    split_id=split.id,
                    join_id=join_id,
                    operator=operator,
                )
            )
    return pairs


def soundness_injections(
    diagram: BpmnDiagram,
    *,
    seed_id: str,
    random_seed: int,
    limit_per_operator: int = 1,
) -> list[Injection]:
    """Select reproducibly, with at most one site per operator and seed."""
    candidates = soundness_candidate_injections(diagram)
    return _select_injections(
        candidates, seed_id, random_seed, limit_per_operator
    )


def soundness_candidate_injections(diagram: BpmnDiagram) -> list[Injection]:
    """Return every independently applicable F01-F04 injection."""
    injections: list[Injection] = []
    for site in soundness_sites(diagram):
        _, join = _find_node(diagram, site.join_id)
        new_type = (
            FlowNodeType.PARALLEL_GATEWAY
            if site.operator is OperatorId.XOR_SPLIT_AND_JOIN
            else FlowNodeType.EXCLUSIVE_GATEWAY
        )
        change = ChangeGatewayTypeOp(id=join.id, new_type=new_type)
        updated, results = apply_edit_ops([change], diagram)
        if not results[0].applied or validate(updated).issues:
            continue
        injections.append(
            Injection(
                operator=site.operator,
                defect_class=DefectClass.SOUND,
                site=(site.split_id, site.join_id),
                expected_finding=(
                    "deadlock"
                    if site.operator is OperatorId.XOR_SPLIT_AND_JOIN
                    else "lack_of_synchronisation"
                ),
                expected_elements=[site.split_id, site.join_id],
                injection=[change],
                repair=[ChangeGatewayTypeOp(id=join.id, new_type=join.type)],
                diagram=updated,
            )
        )
    injections.extend(_improper_completion_injections(diagram))
    injections.extend(_bridge_flow_injections(diagram))
    return sorted(injections, key=lambda item: (item.operator, item.site))


def _improper_completion_injections(diagram: BpmnDiagram) -> list[Injection]:
    injections: list[Injection] = []
    for site in soundness_sites(diagram):
        if site.operator is not OperatorId.AND_SPLIT_XOR_JOIN:
            continue
        process, join = _find_node(diagram, site.join_id)
        if join.extra or join.event_definitions or len(join.outgoing) != 1:
            continue
        outgoing = next(
            flow for flow in process.sequence_flows if flow.id == join.outgoing[0]
        )
        _, target = _find_node(diagram, outgoing.target_ref)
        if target.type is not FlowNodeType.END_EVENT:
            continue
        incoming = sorted(
            (
                flow
                for flow in process.sequence_flows
                if flow.id in set(join.incoming)
            ),
            key=lambda flow: flow.id,
        )
        injection_ops: list[AtomicEditOp] = [
            RemoveNodeOp(id=join.id, cascade=True)
        ]
        injection_ops.extend(
            AddFlowOp(
                process_id=process.id,
                id=flow.id,
                source_ref=flow.source_ref,
                target_ref=target.id,
                name=flow.name,
                condition_expression=flow.condition_expression,
            )
            for flow in incoming
        )
        updated, results = apply_edit_ops(injection_ops, diagram)
        if not all(result.applied for result in results) or validate(updated).issues:
            continue

        repair: list[AtomicEditOp] = [
            RemoveFlowOp(id=flow.id) for flow in incoming
        ]
        repair.append(
            AddNodeOp(
                id=join.id,
                node_type=join.type,
                process_id=process.id,
                name=join.name,
            )
        )
        repair.extend(
            _restore_flow(process.id, flow) for flow in [*incoming, outgoing]
        )
        injections.append(
            Injection(
                operator=OperatorId.DELETE_PARALLEL_JOIN,
                defect_class=DefectClass.SOUND,
                site=(site.split_id, join.id, target.id),
                expected_finding="improper_completion",
                expected_elements=[site.split_id, join.id, target.id],
                injection=injection_ops,
                repair=repair,
                diagram=updated,
            )
        )
    return injections


def _bridge_flow_injections(diagram: BpmnDiagram) -> list[Injection]:
    injections: list[Injection] = []
    for process in diagram.processes:
        nodes = {node.id: node for node in process.flow_nodes}
        starts = {
            node.id for node in process.flow_nodes
            if node.type is FlowNodeType.START_EVENT
        }
        ends = {
            node.id for node in process.flow_nodes
            if node.type is FlowNodeType.END_EVENT
        }
        if not starts or not ends:
            continue
        for flow in sorted(process.sequence_flows, key=lambda item: item.id):
            if (
                nodes[flow.source_ref].type
                in {FlowNodeType.START_EVENT, FlowNodeType.END_EVENT}
                or nodes[flow.target_ref].type
                in {FlowNodeType.START_EVENT, FlowNodeType.END_EVENT}
            ):
                continue
            successors = {node_id: [] for node_id in nodes}
            predecessors = {node_id: [] for node_id in nodes}
            for other in process.sequence_flows:
                if other.id == flow.id:
                    continue
                successors[other.source_ref].append(other.target_ref)
                predecessors[other.target_ref].append(other.source_ref)
            from_start = _reachable_from(starts, successors)
            to_end = _reachable_from(ends, predecessors)
            if flow.target_ref in from_start or flow.source_ref in to_end:
                continue

            remove = RemoveFlowOp(id=flow.id)
            updated, results = apply_edit_ops([remove], diagram)
            findings = {issue.rule_id for issue in validate(updated).issues}
            if (
                not results[0].applied
                or "R007" not in findings
                or findings & {"R001", "R002", "R003", "R004", "R005", "R006"}
            ):
                continue
            injections.append(
                Injection(
                    operator=OperatorId.DELETE_BRIDGE_FLOW,
                    defect_class=DefectClass.SOUND,
                    site=(flow.id, flow.source_ref, flow.target_ref),
                    expected_finding="R007",
                    expected_elements=[flow.target_ref],
                    injection=[remove],
                    repair=[_restore_flow(process.id, flow)],
                    diagram=updated,
                )
            )
    return injections


def _remove_event(
    diagram: BpmnDiagram,
    process_id: str,
    node: FlowNode,
    operator: OperatorId,
    expected_finding: str,
) -> Injection | None:
    # AddNodeOp cannot restore these fields. Skipping such sites keeps the stored
    # repair an actual inverse over the supported EditOp vocabulary.
    if node.extra or node.event_definitions:
        return None

    process = next(item for item in diagram.processes if item.id == process_id)
    incident = [
        flow
        for flow in process.sequence_flows
        if flow.source_ref == node.id or flow.target_ref == node.id
    ]
    repair: list[AtomicEditOp] = [
        AddNodeOp(
            id=node.id,
            node_type=node.type,
            process_id=process_id,
            name=node.name,
        )
    ]
    repair.extend(_restore_flow(process_id, flow) for flow in incident)

    updated, results = apply_edit_ops(
        [RemoveNodeOp(id=node.id, cascade=True)], diagram
    )
    if not results or not results[0].applied:
        return None

    findings = {issue.rule_id for issue in validate(updated).issues}
    if findings != {expected_finding}:
        return None

    return Injection(
        operator=operator,
        defect_class=DefectClass.STRUCT,
        site=(node.id,),
        expected_finding=expected_finding,
        expected_elements=[process_id],
        injection=[RemoveNodeOp(id=node.id, cascade=True)],
        repair=repair,
        diagram=updated,
    )


def _restore_flow(process_id: str, flow: SequenceFlow) -> AddFlowOp:
    return AddFlowOp(
        process_id=process_id,
        id=flow.id,
        source_ref=flow.source_ref,
        target_ref=flow.target_ref,
        name=flow.name,
        condition_expression=flow.condition_expression,
    )


def _postdominators(
    nodes: dict[str, FlowNode], successors: dict[str, list[str]]
) -> dict[str, set[str]]:
    virtual_exit = "__dataset_exit__"
    universe = set(nodes) | {virtual_exit}
    augmented = {
        node_id: (targets if targets else [virtual_exit])
        for node_id, targets in successors.items()
    }
    augmented[virtual_exit] = []
    postdominators = {
        node_id: ({virtual_exit} if node_id == virtual_exit else set(universe))
        for node_id in universe
    }

    changed = True
    while changed:
        changed = False
        for node_id in sorted(nodes):
            targets = augmented[node_id]
            common = set.intersection(*(postdominators[target] for target in targets))
            updated = {node_id} | common
            if updated != postdominators[node_id]:
                postdominators[node_id] = updated
                changed = True
    return postdominators


def _shortest_distance(
    start: str, target: str, successors: dict[str, list[str]]
) -> int | None:
    queue = [(start, 0)]
    seen = {start}
    for current, distance in queue:
        if current == target:
            return distance
        for next_id in successors[current]:
            if next_id not in seen:
                seen.add(next_id)
                queue.append((next_id, distance + 1))
    return None


def _reachable_before(
    start: str, stop: str, successors: dict[str, list[str]]
) -> set[str]:
    reachable: set[str] = set()
    queue = [start]
    for current in queue:
        if current == stop or current in reachable:
            continue
        reachable.add(current)
        queue.extend(successors[current])
    return reachable


def _reachable_from(
    starts: set[str], successors: dict[str, list[str]]
) -> set[str]:
    reachable: set[str] = set()
    queue = sorted(starts)
    for current in queue:
        if current in reachable:
            continue
        reachable.add(current)
        queue.extend(successors[current])
    return reachable


def _select_injections(
    injections: list[Injection], seed_id: str, random_seed: int, limit: int
) -> list[Injection]:
    ordered = sorted(injections, key=lambda item: (item.operator, item.site))
    material = f"{random_seed}:{seed_id}:SOUND".encode()
    local_seed = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    rng = random.Random(local_seed)
    by_operator: dict[OperatorId, list[Injection]] = {}
    for injection in ordered:
        by_operator.setdefault(injection.operator, []).append(injection)
    selected = [
        injection
        for operator in sorted(by_operator)
        for injection in rng.sample(
            by_operator[operator],
            k=min(limit, len(by_operator[operator])),
        )
    ]
    return sorted(selected, key=lambda item: (item.operator, item.site))


def _injection_footprint(
    diagram: BpmnDiagram, injection: Injection
) -> set[str]:
    """Return the injection site plus its immediate control-flow neighborhood."""
    declared = set(diagram.element_ids())
    footprint = {item for item in injection.site if item in declared}
    for process in diagram.processes:
        flows = {flow.id: flow for flow in process.sequence_flows}
        for flow_id in list(footprint):
            flow = flows.get(flow_id)
            if flow is not None:
                footprint.update((flow.source_ref, flow.target_ref))
        local_nodes = {
            item for item in footprint
            if any(node.id == item for node in process.flow_nodes)
        }
        for flow in process.sequence_flows:
            if flow.source_ref in local_nodes or flow.target_ref in local_nodes:
                footprint.update((flow.id, flow.source_ref, flow.target_ref))
    return footprint


def _expected_issue_present(injection: Injection, issues: list) -> bool:
    expected_elements = set(injection.expected_elements)
    return any(
        issue.rule_id == injection.expected_finding
        and (
            issue.element_id is None
            or not expected_elements
            or issue.element_id in expected_elements
        )
        for issue in issues
    )


def _injection_persists(injection: Injection, diagram: BpmnDiagram) -> bool:
    for op in injection.injection:
        if isinstance(op, RepointFlowOp):
            found = _find_flow(diagram, op.flow_id)
            if found is None or getattr(found[1], op.endpoint) != op.new_ref:
                return False
        elif isinstance(op, ChangeGatewayTypeOp):
            found = _find_node_optional(diagram, op.id)
            if found is None or found[1].type is not op.new_type:
                return False
        elif isinstance(op, RemoveNodeOp):
            if _find_node_optional(diagram, op.id) is not None:
                return False
        elif isinstance(op, RemoveFlowOp):
            if _find_flow(diagram, op.id) is not None:
                return False
        elif isinstance(op, AddFlowOp):
            found = _find_flow(diagram, op.id)
            if (
                found is None
                or found[1].source_ref != op.source_ref
                or found[1].target_ref != op.target_ref
            ):
                return False
    return True


def _without_layout(diagram: BpmnDiagram) -> BpmnDiagram:
    stripped = diagram.model_copy(deep=True)
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


def _find_node(diagram: BpmnDiagram, node_id: str):
    for process in diagram.processes:
        for node in process.flow_nodes:
            if node.id == node_id:
                return process, node
    raise ValueError(f"node not found: {node_id}")


def _find_node_optional(diagram: BpmnDiagram, node_id: str):
    try:
        return _find_node(diagram, node_id)
    except ValueError:
        return None


def _find_flow(diagram: BpmnDiagram, flow_id: str):
    for process in diagram.processes:
        for flow in process.sequence_flows:
            if flow.id == flow_id:
                return process, flow
    return None
