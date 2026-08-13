"""Deterministic ablation dry run against mocked providers.

Responses are canned, so this measures nothing about repair quality; it only shows
that each control reaches the executed path. Run it with `make dry-run OUT=<path>`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.experiments import (
    ExperimentConfig,
    IrFormat,
    RepairMode,
    TiersEnabled,
    app_commit,
    build_run_block,
    canonical_config_json,
    converter_version,
)
from app.llm import client as llm_client
from app.llm.protocol import LlmResponse
from app.llm.router import TaskType, resolve_model
from app.llm.tracing import (
    get_traces,
    models_called,
    reset_trace_context,
    start_trace_context,
    trace_usage,
)
from app.llm.usage import TokenUsage
from app.model.schema import BpmnDiagram
from app.services.diagrams import (
    describe_unsupported,
    parse_bpmn_bytes_with_diagnostics,
)
from app.services.repair import dispatch_repair, repair_prompt_path
from app.services.validation import validate_diagram, validate_prompt_path
from app.validation.checkers import checker_versions
from app.validation.rules import RULES_VERSION, ValidationTier

# ---------------------------------------------------------------------------
# the mocked provider
# ---------------------------------------------------------------------------


@dataclass
class MockProvider:
    """A provider that answers from fixed rules instead of a network call.

    Responses depend only on the request, so repeated dry runs are byte-identical.
    Seed support is declared absent on purpose, to exercise the unsupported path.
    Token counts are synthetic (chars / 4) and labelled `source="mock"`.
    """

    supports_temperature: bool = True
    supports_seed: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def complete(
        self,
        prompt: str,
        system: str | None,
        model: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> LlmResponse:
        self.calls.append({"kind": "complete", "prompt": prompt})
        return _mock_response(prompt, system, "Unstructured mock response.")

    async def complete_with_history(
        self,
        messages: list[dict],
        system: str | None,
        model: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> LlmResponse:
        self.calls.append({"kind": "complete_with_history", "messages": messages})
        return _mock_response(
            json.dumps(messages), system, "Acknowledged. No diagram change proposed."
        )

    async def complete_structured_with_history(
        self,
        messages: list[dict],
        system: str | None,
        model: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> LlmResponse:
        self.calls.append(
            {"kind": "complete_structured_with_history", "messages": messages}
        )
        text = json.dumps(
            {
                "description": "Acknowledged. No diagram change proposed.",
                "result": {"diagram": None},
            }
        )
        return _mock_response(json.dumps(messages), system, text)

    async def complete_structured(
        self,
        prompt: str,
        system: str | None,
        model: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> LlmResponse:
        self.calls.append({"kind": "complete_structured", "prompt": prompt})
        definitions = schema.get("$defs", {})
        if "SemanticFindings" in definitions:
            result = {"findings": [_canned_semantic_finding(prompt)]}
        elif "HolisticFindings" in definitions:
            result = {"findings": [_canned_semantic_finding(prompt)]}
        elif "AtomicEditOpsResult" in definitions:
            result = {"ops": _canned_ops(prompt)}
        elif "RegenerationResult" in definitions:
            payload = json.loads(prompt.split("\n\n")[0])
            diagram = payload["diagram"]
            result = {
                "ir": diagram if isinstance(diagram, str) else json.dumps(diagram),
                "unresolved": [],
            }
        elif "RawXmlResult" in definitions:
            result = {"xml": prompt.split("\n\n")[0]}
        else:
            raise AssertionError("unknown structured-response schema")
        text = json.dumps({"description": "Deterministic mock response.", "result": result})
        return _mock_response(prompt, system, text)


def _mock_response(prompt: str, system: str | None, text: str) -> LlmResponse:
    """a response whose synthetic token counts scale with the payload

    Derived rather than pinned, so an `ir_format` that stopped changing the prompt shows.
    """
    prompt_chars = len(prompt) + len(system or "")
    return LlmResponse(
        text,
        TokenUsage(
            input_tokens=prompt_chars // 4,
            output_tokens=len(text) // 4,
            source="mock",
        ),
    )


def _canned_semantic_finding(prompt: str) -> dict[str, Any]:
    """one tier-3 finding, pinned to an element that exists in the diagram"""
    payload = json.loads(prompt)
    element_refs = _first_node_id(payload)
    return {
        "category": "missing_step",
        "severity": "warning",
        "message": "Dry-run semantic finding.",
        "element_refs": [element_refs] if element_refs else [],
        "suggestion": "No action; this is a mocked response.",
    }


def _canned_ops(prompt: str) -> list[dict[str, Any]]:
    """a rename, which always applies and always changes the diagram

    The new name carries a diagram digest; a constant name would stall the loop at
    `repeated_state` and make the iteration budgets indistinguishable.
    """
    payload = json.loads(prompt)
    node_ids = payload.get("id_constraints", {}).get("node_ids", [])
    if not node_ids:
        return []
    return [
        {
            "op": "rename_node",
            "id": node_ids[0],
            "new_name": f"Renamed by dry run {_diagram_digest(payload)}",
        }
    ]


def _diagram_digest(payload: dict[str, Any]) -> str:
    """a short, stable identity for the diagram this plan was asked about"""
    diagram = payload.get("diagram")
    text = diagram if isinstance(diagram, str) else json.dumps(diagram, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _first_node_id(payload: dict[str, Any]) -> str | None:
    diagram = payload.get("diagram")
    if not isinstance(diagram, dict):
        return None
    for process in diagram.get("processes", []):
        for node in process.get("flow_nodes", []):
            node_id = node.get("id")
            if node_id:
                return str(node_id)
    return None


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------


def _tier_subsets() -> list[TiersEnabled]:
    """all seven non-empty subsets of {t1, t2, t3}"""
    return [
        TiersEnabled(t1=t1, t2=t2, t3=t3)
        for t1 in (True, False)
        for t2 in (True, False)
        for t3 in (True, False)
        if t1 or t2 or t3
    ]


def build_matrix() -> list[tuple[str, ExperimentConfig]]:
    """one configuration per control setting, labelled by what it varies"""
    cases: list[tuple[str, ExperimentConfig]] = []

    for tiers in _tier_subsets():
        label = "".join(
            name for name, on in (("t1", tiers.t1), ("t2", tiers.t2), ("t3", tiers.t3)) if on
        )
        cases.append((f"tiers:{label}", ExperimentConfig(tiers_enabled=tiers)))

    for ir_format in IrFormat:
        cases.append(
            (f"ir_format:{ir_format.value}", ExperimentConfig(ir_format=ir_format))
        )

    for repair_mode in RepairMode:
        cases.append(
            (
                f"repair_mode:{repair_mode.value}",
                ExperimentConfig(repair_mode=repair_mode),
            )
        )

    for enabled in (True, False):
        cases.append(
            (
                f"formal_evidence:{'on' if enabled else 'off'}",
                ExperimentConfig(
                    tiers_enabled=TiersEnabled(t1=True, t2=True),
                    include_formal_evidence=enabled,
                ),
            )
        )

    for iters in (1, 2, 3, 5, 10):
        cases.append(
            (f"max_repair_iters:{iters}", ExperimentConfig(max_repair_iters=iters))
        )

    cases.append(
        (
            "sampling:temperature+seed",
            ExperimentConfig(temperature=0.2, seed=1234),
        )
    )
    cases.append(("sampling:unset", ExperimentConfig()))

    return cases


async def run_case(
    label: str,
    config: ExperimentConfig,
    diagram: BpmnDiagram,
    provider: MockProvider,
) -> dict[str, Any]:
    """execute one configuration and record what it actually did"""
    provider.calls.clear()
    token = start_trace_context()
    try:
        validation = await validate_diagram(diagram, config=config)
        issues = validation.issues + validation.semantic_issues
        repair = (
            await dispatch_repair(diagram, issues=issues, config=config)
            if issues
            else None
        )
        traces = get_traces()
    finally:
        reset_trace_context(token)

    tiers_observed = sorted(
        {str(issue.tier) for issue in issues if issue.tier is not None}
    )
    prompts = [
        trace.prompt or json.dumps(trace.messages or [])
        for trace in traces
    ]

    run = build_run_block(
        config=config,
        model_used=models_called(traces),
        model_configured=resolve_model(TaskType.REPAIR, config=config),
        converter=converter_version(config),
        rules_version=RULES_VERSION,
        prompt_files={
            "repair": repair_prompt_path(config),
            **({"validate": validate_prompt_path()} if config.tiers_enabled.t3 else {}),
        },
        checkers=checker_versions(config) if config.tiers_enabled.t2 else None,
        iterations=repair.iterations if repair else 0,
        converged=repair.converged if repair else True,
    )

    return {
        "case": label,
        "config": json.loads(canonical_config_json(config)),
        "run": run.model_dump(mode="json"),
        # evidence that the control changed the executed path, not just the hash
        "executed": {
            "tiers_observed": tiers_observed,
            "tier1_ran": ValidationTier.TIER1.value in tiers_observed,
            "tier2_ran": ValidationTier.TIER2.value in tiers_observed,
            "tier3_ran": ValidationTier.TIER3.value in tiers_observed,
            "llm_calls": len(traces),
            "call_kinds": sorted({trace.kind for trace in traces}),
            # synthetic counts, but they show usage survives adapter -> trace
            "usage": trace_usage(traces).model_dump(mode="json"),
            # what the selected `ir_format` put on the wire, for token-reduction comparisons
            "prompt_chars": sum(len(prompt) for prompt in prompts),
            "formal_evidence_in_prompt": any(
                "formal_evidence" in prompt for prompt in prompts
            ),
            "sampling_honored": {
                "temperature": [trace.temperature for trace in traces],
                "seed": [trace.seed for trace in traces],
            },
            "sampling_unsupported": sorted(
                {
                    control
                    for trace in traces
                    for control in trace.unsupported_controls
                }
            ),
        },
        "result": {
            "issue_count": len(issues),
            "issue_ids": sorted(issue.rule_id for issue in issues),
            "iterations": repair.iterations if repair else 0,
            "converged": repair.converged if repair else True,
            "errors_resolved": repair.errors_resolved if repair else True,
            "stop_reason": str(repair.stop_reason) if repair else "converged",
            "applied_ops": [op.op for op in repair.applied_ops] if repair else [],
            "applied_op_origins": (
                [str(origin) for origin in repair.applied_op_origins] if repair else []
            ),
            "failed_ops": (
                [
                    {
                        "op": result.op.op,
                        "origin": str(origin),
                        "error": result.error,
                    }
                    for result, origin in zip(
                        repair.failed_ops, repair.failed_op_origins, strict=True
                    )
                ]
                if repair
                else []
            ),
        },
    }


# two fixtures, since one cannot reach every control: R001 takes the quick-fix path
# and never calls a model, the expense diagram fires tiers 1 and 2 together
DEFAULT_INPUTS = (
    Path("data/rule_cases/R001_no_start_event.broken.bpmn"),
    Path("data/test_cases/03_expense_reimbursement.bpmn"),
)


async def execute(input_paths: tuple[Path, ...] | list[Path]) -> dict[str, Any]:
    """run the whole matrix against each input and return the record"""
    provider = MockProvider()
    cases = []
    # what each fixture lost on import
    diagnostics: dict[str, Any] = {}
    # patched here so the dry run cannot accidentally resolve a real registered provider
    original_get_provider = llm_client.get_provider
    llm_client.get_provider = lambda name=None: provider
    try:
        for input_path in input_paths:
            diagram, unsupported = parse_bpmn_bytes_with_diagnostics(
                input_path.read_bytes()
            )
            diagnostics[str(input_path)] = {
                "unsupported_elements": [asdict(element) for element in unsupported],
                "unsupported_warning": describe_unsupported(unsupported),
            }
            for label, config in build_matrix():
                case = await run_case(label, config, diagram, provider)
                case["input"] = str(input_path)
                cases.append(case)
    finally:
        llm_client.get_provider = original_get_provider

    return {
        "kind": "ablation_dry_run",
        "app_commit": app_commit(),
        "generated_at": datetime.now(UTC).isoformat(),
        "inputs": [str(path) for path in input_paths],
        "input_diagnostics": diagnostics,
        "provider": "mocked — no API call was made",
        "case_count": len(cases),
        "cases": cases,
    }
