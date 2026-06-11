"""Tier-2 formal checker orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml

from app.experiments import ExperimentConfig, T2Tool
from app.model.formats.pydantic_ir import PydanticConverter
from app.model.schema import BpmnDiagram
from app.validation.rules import FormalWitness, Severity, ValidationIssue

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
    dead_tasks = _dead_tasks(diagnostics)
    description = "; ".join(messages) if messages else "Woflan reported an unsound process."
    return [
        ValidationIssue(
            rule_id="woflan:soundness",
            severity=Severity.ERROR,
            message=description,
            element_refs=dead_tasks,
            source=T2Tool.WOFLAN.value,
            formal_witness=FormalWitness(
                kind="soundness",
                description=description,
            ),
            raw={
                "sound": False,
                "diagnostic_messages": messages,
                "dead_tasks": dead_tasks,
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


def _dead_tasks(diagnostics: dict[Any, Any]) -> list[str]:
    tasks = diagnostics.get("dead_tasks") or []
    refs: list[str] = []
    for task in tasks:
        ref = getattr(task, "label", None) or getattr(task, "name", None) or str(task)
        if ref:
            refs.append(str(ref))
    return refs


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
