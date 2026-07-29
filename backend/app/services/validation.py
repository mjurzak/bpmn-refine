"""Reusable validation orchestration for API routes and CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from app.experiments import ExperimentConfig
from app.llm import client as llm_client
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


class ValidationResult(BaseModel):
    is_valid: bool
    issues: list[ValidationIssue]
    semantic_issues: list[ValidationIssue] = []


class SemanticFinding(BaseModel):
    """One tier-3 finding, in the shape the provider is constrained to produce."""

    category: SemanticCategory = Field(
        description="Which kind of semantic defect this is."
    )
    severity: Severity = Field(
        description="'error' if the process cannot execute correctly as modeled, "
        "otherwise 'warning'."
    )
    message: str = Field(description="Concise description of the issue.")
    element_refs: list[str] = Field(
        default_factory=list,
        description="IDs of the affected BPMN elements. Empty for a process-wide "
        "finding. Every ID must exist in the diagram.",
    )
    suggestion: str | None = Field(
        default=None, description="Actionable fix suggestion."
    )


class SemanticFindings(BaseModel):
    """Envelope: structured-output responses must be a top-level object."""

    findings: list[SemanticFinding] = Field(default_factory=list)


# computed once — no open dicts in the model, so it is fully strict-expressible
_SEMANTIC_SCHEMA = strict_json_schema(SemanticFindings)


async def validate_diagram(
    diagram: BpmnDiagram,
    include_semantic: bool | None = None,
    include_t2: bool | None = None,
    include_t1: bool | None = None,
    config: ExperimentConfig | None = None,
) -> ValidationResult:
    """run deterministic validation and optionally an LLM semantic pass

    All three tier switches follow the same rule: an explicit argument wins,
    otherwise the config decides. Tier 3 used to ignore the config, so a caller
    could enable it here and have the repair loop — which only reads the config —
    silently skip the semantic re-check. Tier 1 ignored it in the other
    direction: it ran unconditionally, so `tiers_enabled.t1=false` was an
    advertised control that changed nothing and made three of the seven tier
    subsets unreachable.
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
        # tier 3 runs last on purpose: it is handed everything tiers 1 and 2 already
        # found, so it can skip re-reporting them and spend its budget on what only
        # a reader can see
        semantic_issues = await _semantic_validate(
            diagram,
            rule_issues + checker_issues,
            config=active_config,
        )

    issues = rule_issues + checker_issues
    all_issues = issues + semantic_issues
    is_valid = not any(issue.severity == "error" for issue in all_issues)
    return ValidationResult(
        is_valid=is_valid,
        issues=issues,
        semantic_issues=semantic_issues,
    )


async def _semantic_validate(
    diagram: BpmnDiagram,
    existing_issues: list[ValidationIssue],
    config: ExperimentConfig | None = None,
) -> list[ValidationIssue]:
    active_config = config or ExperimentConfig()
    system_prompt = render_prompt_template(_VALIDATE_PROMPT, config=config)
    payload = {
        "ir_format": str(active_config.ir_format),
        "diagram": diagram_payload(diagram, config),
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
            schema=_SEMANTIC_SCHEMA,
            system=system_prompt,
            model=resolve_model(TaskType.SEMANTIC_VALIDATION, config=config),
            provider=resolve_provider(TaskType.SEMANTIC_VALIDATION, config=config),
            reasoning_effort=str(config.reasoning_effort) if config and config.reasoning_effort else None,
            **resolve_sampling(config),
        )
        findings = SemanticFindings.model_validate(
            parsed if isinstance(parsed, dict) else {"findings": parsed}
        )
    except Exception:
        # surface malformed LLM output as a warning instead of crashing the command
        return [
            ValidationIssue(
                rule_id="LLM_PARSE_ERROR",
                severity=Severity.WARNING,
                message="LLM semantic validation returned an unparseable response.",
                tier=ValidationTier.TIER3,
                source=SOURCE_LLM,
            )
        ]

    return _normalise_semantic_findings(findings.findings, diagram, existing_issues)


def _normalise_semantic_findings(
    findings: list[SemanticFinding],
    diagram: BpmnDiagram,
    existing_issues: list[ValidationIssue],
) -> list[ValidationIssue]:
    """turn model findings into issues the rest of the pipeline can rely on"""
    known_ids = set(diagram.element_ids())
    already_flagged = _elements_named_by(existing_issues)
    seen: set[tuple[str, tuple[str, ...]]] = set()
    issues: list[ValidationIssue] = []

    for finding in findings:
        # a reference the diagram does not contain points the user, and any
        # follow-up repair prompt, at an element that is not there
        refs = [ref for ref in finding.element_refs if ref in known_ids]
        rule_id = semantic_rule_id(finding.category)

        # the prompt asks for two severities; `info` is defined as "omit it"
        severity = (
            Severity.ERROR if finding.severity == Severity.ERROR else Severity.WARNING
        )

        # the same category on the same elements twice in one response
        key = (rule_id, tuple(sorted(refs)))
        if key in seen:
            continue
        seen.add(key)

        # a structural verdict on an element tier 1 or 2 already named is a
        # restatement of theirs, however differently it is worded
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
            )
        )

    return issues


# categories that overlap what tiers 1 and 2 already decide structurally. A
# semantic finding of this kind on an element an earlier tier already flagged is
# a restatement, however differently it is worded.
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


def validate_prompt_name() -> str:
    return _VALIDATE_PROMPT.name


def validate_prompt_path() -> Path:
    return _VALIDATE_PROMPT
