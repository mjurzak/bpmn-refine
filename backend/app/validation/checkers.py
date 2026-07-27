"""Tier-2 formal checker orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml

from app.experiments import ExperimentConfig, T2Tool
from app.model.formats.pydantic_ir import PydanticConverter
from app.model.schema import BpmnDiagram
from app.validation.rules import (
    FormalWitness,
    Severity,
    ValidationIssue,
    ValidationTier,
)

CHECKERS_CONFIG_PATH = Path(__file__).with_name("checkers.yaml")


async def run_tier2_checkers(
    diagram: BpmnDiagram,
    config: ExperimentConfig,
) -> list[ValidationIssue]:
    """run enabled tier-2 checkers and merge their normalized issues"""
    checker_config = load_checker_config()
    issues: list[ValidationIssue] = []

    if _is_selected(T2Tool.WOFLAN, config) and _is_enabled(T2Tool.WOFLAN, checker_config):
        timeout_ms = _timeout_ms(T2Tool.WOFLAN, checker_config)
        try:
            issues.extend(
                await asyncio.wait_for(
                    asyncio.to_thread(run_woflan, diagram),
                    timeout=timeout_ms / 1000,
                )
            )
        except Exception as exc:
            issues.append(_checker_runtime_issue(T2Tool.WOFLAN, exc))

    # Tier provenance belongs to the orchestration layer, not individual
    # adapters. Future formal checkers therefore inherit the same prompt and UI
    # contract even if they do not stamp their own issues.
    for issue in issues:
        issue.tier = ValidationTier.TIER2
    return _deduplicate(issues)


def checker_versions(config: ExperimentConfig) -> dict[str, str]:
    """return versions for selected, globally enabled checker tools"""
    checker_config = load_checker_config()
    versions: dict[str, str] = {}

    if _is_selected(T2Tool.WOFLAN, config) and _is_enabled(T2Tool.WOFLAN, checker_config):
        versions[T2Tool.WOFLAN.value] = _woflan_version()

    return versions


def load_checker_config(path: Path = CHECKERS_CONFIG_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return data if isinstance(data, dict) else {}


def run_woflan(diagram: BpmnDiagram) -> list[ValidationIssue]:
    """run PM4Py Woflan against the canonical diagram serialized as BPMN XML"""
    import pm4py
    from pm4py.algo.analysis.woflan import algorithm as woflan
    from pm4py.objects.conversion.bpmn import converter as bpmn_converter

    xml = PydanticConverter().serialize(diagram)
    with NamedTemporaryFile(suffix=".bpmn") as handle:
        handle.write(xml)
        handle.flush()
        bpmn_graph = pm4py.read_bpmn(handle.name)

    net, initial_marking, final_marking = bpmn_converter.apply(bpmn_graph)
    result = woflan.apply(
        net,
        initial_marking,
        final_marking,
        parameters={
            woflan.Parameters.PRINT_DIAGNOSTICS: False,
            woflan.Parameters.RETURN_DIAGNOSTICS: True,
            woflan.Parameters.RETURN_ASAP_WHEN_NOT_SOUND: False,
        },
    )
    sound, diagnostics = _parse_woflan_result(result)
    if sound:
        return []

    messages = _diagnostic_messages(diagnostics)
    index = _element_index(diagram)
    dead_names = _petri_names(diagnostics.get("dead_tasks"))
    uncovered_names = _petri_names(diagnostics.get("uncovered_places_s_component"))
    dead_refs = _resolve_petri_names(dead_names, index)
    uncovered_refs = _resolve_petri_names(uncovered_names, index)

    # dead elements first — they are a direct verdict, the s-component places
    # are a weaker localisation hint
    element_refs = dead_refs + [ref for ref in uncovered_refs if ref not in dead_refs]
    description = _soundness_description(dead_refs, uncovered_refs, index)
    return [
        ValidationIssue(
            rule_id="woflan:soundness",
            severity=Severity.ERROR,
            message=description,
            tier=ValidationTier.TIER2,
            element_refs=element_refs,
            source=T2Tool.WOFLAN.value,
            formal_witness=FormalWitness(
                kind="soundness",
                description=description,
            ),
            raw={
                "sound": False,
                "diagnostic_messages": messages,
                "dead_tasks": dead_refs,
                "uncovered_places_s_component": uncovered_refs,
                # keep the untranslated pm4py names so a run stays reproducible
                # against the tool's own output
                "petri_net_names": {
                    "dead_tasks": dead_names,
                    "uncovered_places_s_component": uncovered_names,
                },
            },
        )
    ]


def _parse_woflan_result(result: Any) -> tuple[bool, dict[Any, Any]]:
    if isinstance(result, tuple):
        sound = bool(result[0])
        diagnostics = result[1] if len(result) > 1 and isinstance(result[1], dict) else {}
        return sound, diagnostics
    return bool(result), {}


def _diagnostic_messages(diagnostics: dict[Any, Any]) -> list[str]:
    values: list[str] = []
    for key, value in diagnostics.items():
        if str(key).endswith("DIAGNOSTIC_MESSAGES") or str(key) == "diagnostic_messages":
            if isinstance(value, list):
                values.extend(str(item) for item in value)
            elif value:
                values.append(str(value))
    return values


# pm4py's BPMN -> Petri net converter derives every place and transition name
# from the BPMN element it came from: transitions are named after node ids,
# sequence flows become places keyed by flow id, and a node contributes
# `ent_<id>` / `exi_<id>` places plus `sfl_<id>` / `tfl_<id>` invisible
# transitions. Everything outside that scheme belongs to the encoding, not to
# the user's diagram.
_PETRI_ID_PREFIXES = ("ent_", "exi_", "sfl_", "tfl_")

# the workflow-net source/sink, and the transition Woflan itself adds to
# short-circuit the net before checking soundness
_SYNTHETIC_PETRI_NAMES = frozenset({"source", "sink", "short_circuited_transition"})


@dataclass(frozen=True)
class _ElementIndex:
    """the BPMN ids a Petri-net name is allowed to resolve to, with their labels"""

    ids: frozenset[str]
    names: dict[str, str]


def _element_index(diagram: BpmnDiagram) -> _ElementIndex:
    ids: set[str] = set()
    names: dict[str, str] = {}
    for process in diagram.processes:
        for node in process.flow_nodes:
            ids.add(node.id)
            if node.name:
                names[node.id] = node.name
        for flow in process.sequence_flows:
            ids.add(flow.id)
            if flow.name:
                names[flow.id] = flow.name
    return _ElementIndex(ids=frozenset(ids), names=names)


def _resolve_petri_name(name: str, index: _ElementIndex) -> str | None:
    """map one pm4py place/transition name back to the BPMN element it encodes

    returns None for anything the encoding invented — source/sink, Woflan's
    short-circuit transition, and the uuid4-named invisible transitions pm4py
    emits for gateway splits and joins. Those have no counterpart on the canvas,
    so reporting them would highlight a phantom and hand the repair prompt an
    element that does not exist.
    """
    if name in _SYNTHETIC_PETRI_NAMES:
        return None
    if name in index.ids:
        return name
    for prefix in _PETRI_ID_PREFIXES:
        if name.startswith(prefix):
            candidate = name[len(prefix) :]
            if candidate in index.ids:
                return candidate
    return None


def _resolve_petri_names(names: list[str], index: _ElementIndex) -> list[str]:
    """resolve names to BPMN ids, dropping the unmappable and keeping order"""
    refs: list[str] = []
    for name in names:
        ref = _resolve_petri_name(name, index)
        if ref is not None and ref not in refs:
            refs.append(ref)
    return refs


def _petri_names(entries: Any) -> list[str]:
    """read the `name` off pm4py Place/Transition objects

    deliberately not `label` — for a task, pm4py puts the human-readable name
    there and the BPMN id in `name`, and it is the id we need to map back.
    """
    if not entries:
        return []
    names: list[str] = []
    for entry in entries:
        name = getattr(entry, "name", None)
        if name:
            names.append(str(name))
    return names


def _describe_element(ref: str, index: _ElementIndex) -> str:
    name = index.names.get(ref)
    return f"{name} ({ref})" if name else ref


def _soundness_description(
    dead_refs: list[str],
    uncovered_refs: list[str],
    index: _ElementIndex,
) -> str:
    """phrase the verdict in BPMN terms rather than in Petri-net place names"""
    parts = ["The process is not sound."]
    if dead_refs:
        listed = ", ".join(_describe_element(ref, index) for ref in dead_refs)
        parts.append(f"These elements can never execute: {listed}.")
    if uncovered_refs:
        listed = ", ".join(_describe_element(ref, index) for ref in uncovered_refs)
        parts.append(
            "The following elements are not covered by an S-component, "
            f"which points at a split/join mismatch around them: {listed}."
        )
    if not dead_refs and not uncovered_refs:
        parts.append(
            "The violation could not be localised to a specific element; "
            "see the raw checker diagnostics."
        )
    return " ".join(parts)


def _woflan_version() -> str:
    import pm4py

    return f"pm4py-{pm4py.__version__}"


def _is_selected(tool: T2Tool, config: ExperimentConfig) -> bool:
    return tool in config.t2_tools


def _is_enabled(tool: T2Tool, checker_config: dict[str, dict[str, Any]]) -> bool:
    return bool(checker_config.get(tool.value, {}).get("enabled", False))


def _timeout_ms(tool: T2Tool, checker_config: dict[str, dict[str, Any]]) -> int:
    value = checker_config.get(tool.value, {}).get("timeout_ms", 5000)
    return int(value)


def _checker_runtime_issue(tool: T2Tool, exc: Exception) -> ValidationIssue:
    return ValidationIssue(
        rule_id=f"{tool.value}:runtime_error",
        severity=Severity.WARNING,
        message=f"{tool.value} checker failed: {exc}",
        tier=ValidationTier.TIER2,
        source=tool.value,
        raw={"error": str(exc)},
    )


def _deduplicate(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    seen: set[tuple[tuple[str, ...], str]] = set()
    unique: list[ValidationIssue] = []
    for issue in issues:
        key = (tuple(sorted(issue.element_refs or ([issue.element_id] if issue.element_id else []))), issue.rule_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique
