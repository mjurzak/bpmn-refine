"""Reusable validation orchestration for API routes and CLI commands."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.experiments import ExperimentConfig, LlmValidationScope
from app.llm import client as llm_client
from app.llm.envelope import LlmResponseEnvelope
from app.llm.prompt_context import render_prompt_template
from app.llm.router import TaskType, resolve_model, resolve_provider, resolve_sampling
from app.llm.schema import strict_json_schema
from app.model.schema import BpmnDiagram
from app.services.ir_payload import diagram_payload
from app.validation.checkers import run_tier2_checkers
from app.validation.rules import (
    SOURCE_LLM,
    SemanticCategory,
    Severity,
    ValidationIssue,
    ValidationReport,
    ValidationTier,
    issue_to_dict,
    semantic_rule_id,
    validate,
)

_PROMPT_DIR = Path(__file__).parent.parent / "llm" / "prompts"
_VALIDATE_PROMPT = _PROMPT_DIR / "validate.txt"
_HOLISTIC_VALIDATE_PROMPT = _PROMPT_DIR / "validate_holistic.txt"


class ValidationResult(BaseModel):
    is_valid: bool
    issues: list[ValidationIssue]
    semantic_issues: list[ValidationIssue] = []


class SemanticSeverity(StrEnum):
    """Only severities that Tier 3 is allowed to emit."""

    ERROR = "error"
    WARNING = "warning"


class SemanticFinding(BaseModel):
    """One tier-3 finding, in the shape the provider is constrained to produce."""

    category: SemanticCategory = Field(
        description="Which kind of semantic defect this is."
    )
    severity: SemanticSeverity = Field(
        description="'error' if the process cannot execute correctly as modeled, "
        "otherwise 'warning'."
    )
    message: str = Field(description="Concise description of the issue.")
    element_refs: list[str] = Field(
        default_factory=list,
        description="IDs of the affected BPMN elements. Empty for a process-wide "
        "finding. Every ID must exist in the diagram.",
    )
    reference_evidence: list[str] = Field(
        default_factory=list,
        description="Numbered reference-description lines (for example 'L3') that "
        "explicitly support the finding. Empty only when no reference description "
        "was supplied and the contradiction is internal to the diagram.",
    )
    suggestion: str | None = Field(
        default=None, description="Actionable fix suggestion."
    )


class SemanticFindings(BaseModel):
    findings: list[SemanticFinding] = Field(default_factory=list)


SemanticResponse = LlmResponseEnvelope[SemanticFindings]
_SEMANTIC_SCHEMA = strict_json_schema(SemanticResponse)


class HolisticCategory(StrEnum):
    """Closed taxonomy for the LLM-only holistic validation ablation."""

    MISSING_START_EVENT = "missing_start_event"
    MISSING_END_EVENT = "missing_end_event"
    DANGLING_REFERENCE = "dangling_reference"
    DEADLOCK = "deadlock"
    LACK_OF_SYNCHRONIZATION = "lack_of_synchronization"
    IMPROPER_COMPLETION = "improper_completion"
    UNREACHABLE_REGION = "unreachable_region"
    MISSING_STEP = SemanticCategory.MISSING_STEP.value
    CONTRADICTORY_FLOW = SemanticCategory.CONTRADICTORY_FLOW.value
    UNREACHABLE_BRANCH = SemanticCategory.UNREACHABLE_BRANCH.value
    MISSING_EXCEPTION_HANDLING = SemanticCategory.MISSING_EXCEPTION_HANDLING.value
    INCONSISTENT_NAMING = SemanticCategory.INCONSISTENT_NAMING.value
    IMPROPER_TERMINATION = SemanticCategory.IMPROPER_TERMINATION.value
    UNWANTED_ACTION = SemanticCategory.UNWANTED_ACTION.value


class HolisticFinding(BaseModel):
    """One finding from the complete structural and semantic taxonomy."""

    category: HolisticCategory = Field(description="The defect taxonomy category.")
    severity: Severity = Field(
        description="'error' when execution or completion is blocked, otherwise 'warning'."
    )
    message: str = Field(description="Concise description of the issue.")
    element_refs: list[str] = Field(
        default_factory=list,
        description="Affected BPMN element IDs. Empty for a process-wide finding.",
    )
    suggestion: str | None = Field(default=None, description="Actionable fix suggestion.")


class HolisticFindings(BaseModel):
    findings: list[HolisticFinding] = Field(default_factory=list)


HolisticResponse = LlmResponseEnvelope[HolisticFindings]
_HOLISTIC_SCHEMA = strict_json_schema(HolisticResponse)


async def validate_diagram(
    diagram: BpmnDiagram,
    include_semantic: bool | None = None,
    include_t2: bool | None = None,
    include_t1: bool | None = None,
    config: ExperimentConfig | None = None,
    reference_description: str | None = None,
) -> ValidationResult:
    """run deterministic validation and optionally an LLM semantic pass

    Every tier switch follows one rule: an explicit argument wins, else the config decides.
    """
    active_config = config or ExperimentConfig()
    rule_issues: list[ValidationIssue] = []
    checker_issues: list[ValidationIssue] = []
    semantic_issues: list[ValidationIssue] = []
    should_run_t1 = (
        include_t1 if include_t1 is not None else active_config.tiers_enabled.t1
    )
    should_run_t2 = (
        include_t2 if include_t2 is not None else active_config.tiers_enabled.t2
    )
    should_run_t3 = (
        include_semantic if include_semantic is not None else active_config.tiers_enabled.t3
    )

    if should_run_t1:
        report: ValidationReport = validate(diagram)
        rule_issues = report.issues

    if should_run_t2:
        checker_issues = await run_tier2_checkers(diagram, active_config)

    if should_run_t3:
        existing_issues = rule_issues + checker_issues
        # Holistic validation uses a separate prompt/schema and keeps findings
        # that overlap with deterministic tiers.
        if active_config.llm_validation_scope == LlmValidationScope.HOLISTIC:
            semantic_issues = await _holistic_validate(
                diagram,
                existing_issues,
                config=active_config,
                reference_description=reference_description,
            )
        else:
            # runs last so it receives tier 1 and 2 findings and can skip re-reporting them
            semantic_issues = await _semantic_validate(
                diagram,
                existing_issues,
                config=active_config,
                reference_description=reference_description,
            )

    issues = _sort_issues_by_severity(rule_issues + checker_issues)
    semantic_issues = _sort_issues_by_severity(semantic_issues)
    all_issues = issues + semantic_issues
    is_valid = not any(issue.severity == "error" for issue in all_issues)
    return ValidationResult(
        is_valid=is_valid,
        issues=issues,
        semantic_issues=semantic_issues,
    )


def _sort_issues_by_severity(
    issues: list[ValidationIssue],
) -> list[ValidationIssue]:
    """return errors before warnings while preserving order within each severity"""
    priority = {
        Severity.ERROR: 0,
        Severity.WARNING: 1,
        Severity.INFO: 2,
    }
    return sorted(issues, key=lambda issue: priority.get(issue.severity, 3))


async def _semantic_validate(
    diagram: BpmnDiagram,
    existing_issues: list[ValidationIssue],
    config: ExperimentConfig | None = None,
    reference_description: str | None = None,
) -> list[ValidationIssue]:
    active_config = config or ExperimentConfig()
    system_prompt = render_prompt_template(_VALIDATE_PROMPT, config=config)
    payload = {
        "ir_format": str(active_config.ir_format),
        "diagram": diagram_payload(diagram, config),
        "reference_description": _number_reference_description(
            reference_description
        ),
        "existing_issues": [
            issue_to_dict(
                issue,
                include_formal_evidence=active_config.include_formal_evidence,
            )
            for issue in existing_issues
        ],
    }
    if active_config.include_semantic_projection:
        payload["semantic_view"] = _semantic_diagram_view(diagram)
    try:
        parsed = await llm_client.complete_structured(
            prompt=json.dumps(payload),
            schema=_SEMANTIC_SCHEMA,
            task=TaskType.SEMANTIC_VALIDATION,
            system=system_prompt,
            model=resolve_model(TaskType.SEMANTIC_VALIDATION, config=config),
            provider=resolve_provider(TaskType.SEMANTIC_VALIDATION, config=config),
            reasoning_effort=str(config.reasoning_effort) if config and config.reasoning_effort else None,
            **resolve_sampling(config),
        )
        response = SemanticResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        # surface malformed LLM output as a warning instead of crashing the command
        return [
            ValidationIssue(
                rule_id="LLM_PARSE_ERROR",
                severity=Severity.WARNING,
                message=(
                    "LLM semantic validation returned an unparseable response: "
                    f"{type(exc).__name__}: {exc}"
                ),
                tier=ValidationTier.TIER3,
                source=SOURCE_LLM,
            )
        ]

    evidence_labels = (
        _reference_line_labels(reference_description)
        if reference_description is not None
        else None
    )
    return _normalise_semantic_findings(
        response.result.findings,
        diagram,
        existing_issues,
        valid_reference_evidence=evidence_labels,
    )


def _normalise_semantic_findings(
    findings: list[SemanticFinding],
    diagram: BpmnDiagram,
    existing_issues: list[ValidationIssue],
    valid_reference_evidence: set[str] | None = None,
) -> list[ValidationIssue]:
    """turn model findings into issues the rest of the pipeline can rely on"""
    known_ids = set(diagram.element_ids())
    already_flagged = _elements_named_by(existing_issues)
    seen: set[tuple[str, tuple[str, ...]]] = set()
    issues: list[ValidationIssue] = []

    for finding in findings:
        evidence = [
            label
            for label in finding.reference_evidence
            if valid_reference_evidence is None
            or label in valid_reference_evidence
        ]
        # A supplied reference is authoritative. Enforce the prompt's evidence
        # gate so an unsupported model thought cannot become a user-facing issue.
        if valid_reference_evidence is not None and not evidence:
            continue

        # drop refs to elements the diagram does not contain
        refs = [ref for ref in finding.element_refs if ref in known_ids]
        rule_id = semantic_rule_id(finding.category)

        # the prompt asks for two severities; `info` is defined as "omit it"
        severity = Severity(str(finding.severity))

        # drop the same category on the same elements twice in one response
        key = (rule_id, tuple(sorted(refs)))
        if key in seen:
            continue
        seen.add(key)

        # a structural verdict on an element tier 1 or 2 already named is a restatement
        if finding.category in _STRUCTURAL_CATEGORIES and any(
            ref in already_flagged for ref in refs
        ):
            continue

        issues.append(
            ValidationIssue(
                rule_id=rule_id,
                severity=severity,
                message=finding.message,
                tier=ValidationTier.TIER3,
                element_id=refs[0] if refs else None,
                element_refs=refs,
                suggestion=finding.suggestion,
                source=SOURCE_LLM,
                raw={"reference_evidence": evidence},
            )
        )

    return issues


def _number_reference_description(description: str | None) -> str | None:
    """Give every non-empty reference line a stable evidence label."""
    if description is None:
        return None
    lines = [line.strip() for line in description.splitlines() if line.strip()]
    return "\n".join(f"L{index}: {line}" for index, line in enumerate(lines, 1))


def _reference_line_labels(description: str) -> set[str]:
    nonempty = (line for line in description.splitlines() if line.strip())
    return {f"L{index}" for index, _ in enumerate(nonempty, 1)}


def _semantic_diagram_view(diagram: BpmnDiagram) -> dict[str, object]:
    """Return a geometry-free view optimized for semantic validation.

    The canonical IR remains in ``diagram`` for auditability and format ablations.
    This projection makes the business graph easy to inspect without layout bounds,
    label bounds, waypoints, namespaces, or other round-trip-only fields.
    """
    processes: list[dict[str, object]] = []
    for process in diagram.processes:
        nodes: list[dict[str, object]] = []
        for node in process.flow_nodes:
            item: dict[str, object] = {
                "id": node.id,
                "type": str(node.type),
                "name": node.name,
                "incoming": node.incoming,
                "outgoing": node.outgoing,
            }
            if node.event_definitions:
                item["event_definitions"] = [
                    {
                        "type": str(definition.type),
                        "id": definition.id,
                        "extra": definition.extra,
                    }
                    for definition in node.event_definitions
                ]
            if node.extra:
                item["extra"] = node.extra
            nodes.append(item)

        flows: list[dict[str, object]] = []
        for flow in process.sequence_flows:
            item = {
                "id": flow.id,
                "source_ref": flow.source_ref,
                "target_ref": flow.target_ref,
                "name": flow.name,
                "condition_expression": flow.condition_expression,
            }
            flows.append(item)

        processes.append(
            {
                "id": process.id,
                "name": process.name,
                "nodes": nodes,
                "flows": flows,
            }
        )
    return {"processes": processes}


async def _holistic_validate(
    diagram: BpmnDiagram,
    existing_issues: list[ValidationIssue],
    config: ExperimentConfig | None = None,
    reference_description: str | None = None,
) -> list[ValidationIssue]:
    active_config = config or ExperimentConfig()
    system_prompt = render_prompt_template(_HOLISTIC_VALIDATE_PROMPT, config=config)
    payload = {
        "ir_format": str(active_config.ir_format),
        "diagram": diagram_payload(diagram, config),
        "reference_description": reference_description,
        "existing_issues": [
            issue_to_dict(
                issue,
                include_formal_evidence=active_config.include_formal_evidence,
            )
            for issue in existing_issues
        ],
    }
    try:
        parsed = await llm_client.complete_structured(
            prompt=json.dumps(payload),
            schema=_HOLISTIC_SCHEMA,
            task=TaskType.SEMANTIC_VALIDATION,
            system=system_prompt,
            model=resolve_model(TaskType.SEMANTIC_VALIDATION, config=config),
            provider=resolve_provider(TaskType.SEMANTIC_VALIDATION, config=config),
            reasoning_effort=str(config.reasoning_effort) if config and config.reasoning_effort else None,
            **resolve_sampling(config),
        )
        response = HolisticResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return [
            ValidationIssue(
                rule_id="LLM_PARSE_ERROR",
                severity=Severity.WARNING,
                message=(
                    "LLM holistic validation returned an unparseable response: "
                    f"{type(exc).__name__}: {exc}"
                ),
                tier=ValidationTier.TIER3,
                source=SOURCE_LLM,
            )
        ]

    return _normalise_holistic_findings(
        response.result.findings, diagram, existing_issues
    )


def _normalise_holistic_findings(
    findings: list[HolisticFinding],
    diagram: BpmnDiagram,
    existing_issues: list[ValidationIssue] | None = None,
) -> list[ValidationIssue]:
    """Stamp holistic findings without suppressing deterministic overlap.

    ``existing_issues`` mirrors the semantic normalizer's call contract. It is
    intentionally unused: overlap is part of the holistic ablation.
    """
    known_ids = set(diagram.element_ids())
    seen: set[tuple[str, tuple[str, ...]]] = set()
    issues: list[ValidationIssue] = []

    for finding in findings:
        refs = [ref for ref in finding.element_refs if ref in known_ids]
        rule_id = f"llm:{finding.category.value}"
        key = (rule_id, tuple(sorted(refs)))
        if key in seen:
            continue
        seen.add(key)
        severity = (
            Severity.ERROR if finding.severity == Severity.ERROR else Severity.WARNING
        )
        issues.append(
            ValidationIssue(
                rule_id=rule_id,
                severity=severity,
                message=finding.message,
                tier=ValidationTier.TIER3,
                element_id=refs[0] if refs else None,
                element_refs=refs,
                suggestion=finding.suggestion,
                source=SOURCE_LLM,
            )
        )

    return issues


# categories that overlap what tiers 1 and 2 already decide structurally
_STRUCTURAL_CATEGORIES = frozenset(
    {
        SemanticCategory.UNREACHABLE_BRANCH,
        SemanticCategory.IMPROPER_TERMINATION,
    }
)


def _elements_named_by(issues: list[ValidationIssue]) -> set[str]:
    named: set[str] = set()
    for issue in issues:
        if issue.element_id:
            named.add(issue.element_id)
        named.update(issue.element_refs)
    return named


def validate_prompt_name(config: ExperimentConfig | None = None) -> str:
    return validate_prompt_path(config).name


def validate_prompt_path(config: ExperimentConfig | None = None) -> Path:
    if config and config.llm_validation_scope == LlmValidationScope.HOLISTIC:
        return _HOLISTIC_VALIDATE_PROMPT
    return _VALIDATE_PROMPT
