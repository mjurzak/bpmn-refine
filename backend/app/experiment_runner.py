"""Sweep runner: turns a declarative spec into a persisted result set.

Trials resume from `results.jsonl` via a deterministic `trial_id`, a trial that
raised is recorded with its error, and each phase gets its own trace context so
tokens are attributable per phase. Run it with `make experiment SPEC=<path> OUT=<dir>`.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, nullcontext
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.experiments import (
    ExperimentConfig,
    RunBlock,
    app_commit,
    build_run_block,
    canonical_config_json,
    config_hash,
    converter_version,
    hash_bytes,
)
from app.llm.tracing import (
    LlmTrace,
    get_traces,
    models_called,
    reset_trace_context,
    start_trace_context,
    trace_usage,
)
from app.llm.usage import UsageTotals
from app.model.protocol import UnsupportedElement
from app.model.schema import BpmnDiagram
from app.services.diagrams import (
    describe_unsupported,
    parse_bpmn_bytes_with_diagnostics,
)
from app.services.repair import dispatch_repair, repair_prompt_path
from app.services.validation import validate_diagram, validate_prompt_path
from app.validation.checkers import checker_versions
from app.validation.rules import RULES_VERSION, Severity, ValidationIssue

RESULTS_FILENAME = "results.jsonl"
MANIFEST_FILENAME = "manifest.json"

_GLOB_CHARS = set("*?[")


# ---------------------------------------------------------------------------
# the spec
# ---------------------------------------------------------------------------


class SweepSpec(BaseModel):
    """a declarative description of one experiment

    `axes` expands as a cartesian product over `base`; `configs` is appended
    afterwards for hand-picked combinations a product cannot express.
    """

    experiment_id: str
    inputs: list[str] = Field(default_factory=list)
    # applied after `inputs` expands, so the manifest records what was excluded
    exclude: list[str] = Field(default_factory=list)
    base: dict[str, Any] = Field(default_factory=dict)
    axes: dict[str, list[Any]] = Field(default_factory=dict)
    configs: list[dict[str, Any]] = Field(default_factory=list)
    # identical trials repeated for variance; the repeat index is part of the trial id
    repeats: int = Field(default=1, ge=1)
    notes: str | None = None


def load_spec(path: Path) -> SweepSpec:
    """read a spec from JSON or YAML"""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return SweepSpec.model_validate(data)


def resolve_inputs(spec: SweepSpec, root: Path) -> list[Path]:
    """expand the spec's input patterns against `root`, keeping order stable"""
    resolved: list[Path] = []
    seen: set[Path] = set()
    for entry in spec.inputs:
        candidates = (
            sorted(root.glob(entry))
            if _GLOB_CHARS & set(entry)
            else [Path(entry) if Path(entry).is_absolute() else root / entry]
        )
        for candidate in candidates:
            if candidate in seen:
                continue
            if not candidate.is_file():
                raise FileNotFoundError(f"input not found: {candidate}")
            seen.add(candidate)
            resolved.append(candidate)

    excluded = {
        path
        for pattern in spec.exclude
        for path in (
            root.glob(pattern)
            if _GLOB_CHARS & set(pattern)
            else [Path(pattern) if Path(pattern).is_absolute() else root / pattern]
        )
    }
    resolved = [path for path in resolved if path not in excluded]

    if not resolved:
        raise ValueError(f"spec {spec.experiment_id!r} resolved to no input files")
    return resolved


def expand_configs(spec: SweepSpec) -> list[ExperimentConfig]:
    """the cartesian product of `axes` over `base`, then any explicit configs"""
    expanded: list[ExperimentConfig] = []
    names = sorted(spec.axes)
    value_lists = [spec.axes[name] for name in names]
    for combination in product(*value_lists) if names else [()]:
        payload = {**spec.base, **dict(zip(names, combination, strict=True))}
        expanded.append(ExperimentConfig.model_validate(payload))
    for explicit in spec.configs:
        expanded.append(ExperimentConfig.model_validate({**spec.base, **explicit}))
    return expanded


# ---------------------------------------------------------------------------
# trials
# ---------------------------------------------------------------------------


class Trial(BaseModel):
    """one input under one configuration, one time"""

    trial_id: str
    experiment_id: str
    input_path: str
    repeat: int
    config: ExperimentConfig


def trial_id(
    experiment_id: str, input_path: Path, config: ExperimentConfig, repeat: int
) -> str:
    """a stable identity for resumption

    Derived from the trial's inputs, not its position, so reordering the spec keeps
    results on disk valid. The app commit is excluded, so a rebuild does not re-run.
    """
    parts = "|".join(
        [
            experiment_id,
            str(input_path),
            canonical_config_json(config),
            str(repeat),
        ]
    )
    return hash_bytes(parts.encode("utf-8"))


def build_trials(
    spec: SweepSpec, inputs: list[Path], configs: list[ExperimentConfig]
) -> list[Trial]:
    trials: list[Trial] = []
    for input_path in inputs:
        for config in configs:
            for repeat in range(spec.repeats):
                trials.append(
                    Trial(
                        trial_id=trial_id(
                            spec.experiment_id, input_path, config, repeat
                        ),
                        experiment_id=spec.experiment_id,
                        input_path=str(input_path),
                        repeat=repeat,
                        config=config,
                    )
                )
    return trials


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


class CallRecord(BaseModel):
    """one provider call, without its payload unless asked for"""

    kind: str
    provider: str | None = None
    model: str
    duration_ms: int
    # what this `ir_format` put on the wire, the baseline for token-reduction numbers
    prompt_chars: int
    usage: dict[str, Any] | None = None
    unsupported_controls: list[str] = Field(default_factory=list)
    error: str | None = None
    # only with --keep-payloads; prompts reproduce from the recorded config anyway
    prompt: str | None = None
    output: str | None = None


class PhaseRecord(BaseModel):
    duration_ms: int
    usage: UsageTotals
    calls: list[CallRecord] = Field(default_factory=list)


class ValidationRecord(BaseModel):
    is_valid: bool
    issue_ids: list[str]
    error_count: int
    warning_count: int
    issues: list[dict[str, Any]] = Field(default_factory=list)


class RepairRecord(BaseModel):
    iterations: int
    converged: bool
    errors_resolved: bool
    stop_reason: str
    applied_ops: list[str] = Field(default_factory=list)
    # aligned with `applied_ops`: quick_fix, model_plan, model_regen
    applied_op_origins: list[str] = Field(default_factory=list)
    failed_ops: list[dict[str, Any]] = Field(default_factory=list)
    remaining_issue_ids: list[str] = Field(default_factory=list)


class TrialRecord(BaseModel):
    trial_id: str
    experiment_id: str
    input_path: str
    input_hash: str
    input_bytes: int
    repeat: int
    # what the import read past; a truncated diagram would otherwise score as clean
    unsupported_elements: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_warning: str | None = None
    run: RunBlock
    started_at: datetime
    duration_ms: int
    pre_validation: ValidationRecord | None = None
    repair: RepairRecord | None = None
    post_validation: ValidationRecord | None = None
    phases: dict[str, PhaseRecord] = Field(default_factory=dict)
    usage: UsageTotals = Field(default_factory=UsageTotals)
    # kept as IR, not XML: the exporter drops what the IR cannot hold, and a graph
    # distance over the export would count those omissions as repair edits
    final_diagram: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _phase(collected: list[LlmTrace]) -> AsyncGenerator[None]:
    """isolate one phase's traces so usage can be attributed to it"""
    token = start_trace_context()
    try:
        yield
    finally:
        collected.extend(get_traces())
        reset_trace_context(token)


def _call_record(trace: LlmTrace, keep_payloads: bool) -> CallRecord:
    prompt = trace.prompt or (json.dumps(trace.messages) if trace.messages else "")
    return CallRecord(
        kind=trace.kind,
        provider=trace.provider,
        model=trace.model,
        duration_ms=trace.duration_ms,
        prompt_chars=len(prompt),
        usage=trace.usage.model_dump(mode="json") if trace.usage else None,
        unsupported_controls=list(trace.unsupported_controls),
        error=trace.error,
        prompt=prompt if keep_payloads else None,
        output=trace.output if keep_payloads else None,
    )


def _phase_record(
    traces: list[LlmTrace], duration_ms: int, keep_payloads: bool
) -> PhaseRecord:
    return PhaseRecord(
        duration_ms=duration_ms,
        usage=trace_usage(traces),
        calls=[_call_record(trace, keep_payloads) for trace in traces],
    )


def _issue_json(issue: ValidationIssue) -> dict[str, Any]:
    """the whole issue, evidence included

    Not `issue_to_dict`: that is the prompt form, which withholds the formal witness
    under the counterexample ablation. Results keep everything the checker produced.
    """
    return json.loads(json.dumps(asdict(issue), default=str))


def _validation_record(
    issues: list[ValidationIssue], is_valid: bool
) -> ValidationRecord:
    return ValidationRecord(
        is_valid=is_valid,
        issue_ids=sorted(issue.rule_id for issue in issues),
        error_count=sum(1 for issue in issues if issue.severity == Severity.ERROR),
        warning_count=sum(1 for issue in issues if issue.severity == Severity.WARNING),
        issues=[_issue_json(issue) for issue in issues],
    )


async def run_trial(trial: Trial, keep_payloads: bool = False) -> TrialRecord:
    """execute one trial and return its record, error included

    Never raises, so one provider failure does not abort the whole sweep.
    """
    config = trial.config
    input_path = Path(trial.input_path)
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    raw = b""

    phases: dict[str, PhaseRecord] = {}
    all_traces: list[LlmTrace] = []
    pre_record: ValidationRecord | None = None
    repair_record: RepairRecord | None = None
    post_record: ValidationRecord | None = None
    final_diagram: BpmnDiagram | None = None
    unsupported: list[UnsupportedElement] = []
    iterations = 0
    converged = True
    error: str | None = None

    try:
        # inside the try, so an input that moved since `resolve_inputs` is a
        # recorded failure rather than an aborted sweep
        raw = input_path.read_bytes()
        diagram, unsupported = parse_bpmn_bytes_with_diagnostics(raw)
        final_diagram = diagram

        traces: list[LlmTrace] = []
        phase_started = time.perf_counter()
        async with _phase(traces):
            validation = await validate_diagram(diagram, config=config)
        phases["validate"] = _phase_record(
            traces, _elapsed_ms(phase_started), keep_payloads
        )
        all_traces.extend(traces)

        issues = validation.issues + validation.semantic_issues
        pre_record = _validation_record(issues, validation.is_valid)

        if issues:
            traces = []
            phase_started = time.perf_counter()
            async with _phase(traces):
                repair = await dispatch_repair(diagram, issues=issues, config=config)
            phases["repair"] = _phase_record(
                traces, _elapsed_ms(phase_started), keep_payloads
            )
            all_traces.extend(traces)

            final_diagram = repair.repaired_diagram
            iterations = repair.iterations
            converged = repair.converged
            repair_record = RepairRecord(
                iterations=repair.iterations,
                converged=repair.converged,
                errors_resolved=repair.errors_resolved,
                stop_reason=str(repair.stop_reason),
                applied_ops=[op.op for op in repair.applied_ops],
                applied_op_origins=[
                    str(origin) for origin in repair.applied_op_origins
                ],
                failed_ops=[
                    {
                        "op": result.op.op,
                        "origin": str(origin),
                        "error": result.error,
                    }
                    for result, origin in zip(
                        repair.failed_ops, repair.failed_op_origins, strict=True
                    )
                ],
                remaining_issue_ids=sorted(
                    issue.rule_id for issue in repair.remaining_issues
                ),
            )

            # the same tiers re-run as an oracle, giving the soundness pass rate
            traces = []
            phase_started = time.perf_counter()
            async with _phase(traces):
                post = await validate_diagram(final_diagram, config=config)
            phases["revalidate"] = _phase_record(
                traces, _elapsed_ms(phase_started), keep_payloads
            )
            all_traces.extend(traces)
            post_record = _validation_record(
                post.issues + post.semantic_issues, post.is_valid
            )
    except Exception as exc:  # noqa: BLE001 - the record is the deliverable
        error = f"{type(exc).__name__}: {exc}"

    return TrialRecord(
        trial_id=trial.trial_id,
        experiment_id=trial.experiment_id,
        input_path=trial.input_path,
        input_hash=hash_bytes(raw),
        input_bytes=len(raw),
        repeat=trial.repeat,
        started_at=started_at,
        unsupported_elements=[asdict(element) for element in unsupported],
        unsupported_warning=describe_unsupported(unsupported),
        run=build_run_block(
            config=config,
            model_used=models_called(all_traces),
            model_configured=_model_for(config),
            converter=converter_version(config),
            rules_version=RULES_VERSION,
            prompt_files={
                "repair": repair_prompt_path(config),
                **(
                    {"validate": validate_prompt_path()}
                    if config.tiers_enabled.t3
                    else {}
                ),
            },
            checkers=checker_versions(config) if config.tiers_enabled.t2 else None,
            request_id=trial.trial_id,
            iterations=iterations,
            converged=converged,
        ),
        duration_ms=_elapsed_ms(started),
        pre_validation=pre_record,
        repair=repair_record,
        post_validation=post_record,
        phases=phases,
        usage=trace_usage(all_traces),
        final_diagram=(
            final_diagram.model_dump(mode="json") if final_diagram is not None else None
        ),
        error=error,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _model_for(config: ExperimentConfig) -> str:
    from app.llm.router import TaskType, resolve_model

    return resolve_model(TaskType.REPAIR, config=config)


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def completed_trial_ids(results_path: Path) -> set[str]:
    """trial ids already on disk, so a resumed sweep does not pay twice

    A malformed trailing line is skipped; a run killed mid-write leaves one behind.
    """
    if not results_path.exists():
        return set()
    seen: set[str] = set()
    with results_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(json.loads(line)["trial_id"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return seen


def write_manifest(
    out_dir: Path,
    spec: SweepSpec,
    inputs: list[Path],
    configs: list[ExperimentConfig],
    trials: list[Trial],
    executed: int,
    skipped: int,
    failed: int,
    started_at: datetime,
) -> Path:
    manifest = {
        "experiment_id": spec.experiment_id,
        "app_commit": app_commit(),
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "spec": spec.model_dump(mode="json"),
        "inputs": [str(path) for path in inputs],
        "input_hashes": {
            str(path): hash_bytes(path.read_bytes()) for path in inputs
        },
        "config_count": len(configs),
        "config_hashes": [config_hash(config) for config in configs],
        "trial_count": len(trials),
        "executed": executed,
        "skipped_already_done": skipped,
        "failed": failed,
        "results_file": RESULTS_FILENAME,
    }
    path = out_dir / MANIFEST_FILENAME
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return path


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------


class SweepSummary(BaseModel):
    experiment_id: str
    app_commit: str
    results_path: Path
    manifest_path: Path
    planned: int
    executed: int
    skipped: int
    failed: int
    usage: UsageTotals


@asynccontextmanager
async def _mocked_providers() -> AsyncGenerator[None]:
    """route every call to the dry run's canned provider, so `--mock` is free"""
    from app.dry_run import MockProvider
    from app.llm import client as llm_client

    provider = MockProvider()
    original = llm_client.get_provider
    llm_client.get_provider = lambda name=None: provider
    try:
        yield
    finally:
        llm_client.get_provider = original


async def execute_sweep(
    spec: SweepSpec,
    out_dir: Path,
    root: Path = Path("."),
    resume: bool = True,
    mock: bool = False,
    limit: int | None = None,
    keep_payloads: bool = False,
    on_trial: Any = None,
) -> SweepSummary:
    """run every trial in the spec, appending each result as it completes"""
    inputs = resolve_inputs(spec, root)
    configs = expand_configs(spec)
    trials = build_trials(spec, inputs, configs)

    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / RESULTS_FILENAME
    already_done = completed_trial_ids(results_path) if resume else set()

    pending = [trial for trial in trials if trial.trial_id not in already_done]
    skipped = len(trials) - len(pending)
    if limit is not None:
        pending = pending[:limit]

    started_at = datetime.now(UTC)
    executed = 0
    failed = 0
    totals: list[UsageTotals] = []

    async with _mocked_providers() if mock else nullcontext():
        # append mode, flushed per line, so a killed sweep keeps what it finished
        with results_path.open("a", encoding="utf-8") as handle:
            for index, trial in enumerate(pending, start=1):
                record = await run_trial(trial, keep_payloads=keep_payloads)
                handle.write(
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n"
                )
                handle.flush()
                executed += 1
                if record.error:
                    failed += 1
                totals.append(record.usage)
                if on_trial is not None:
                    on_trial(index, len(pending), record)

    manifest_path = write_manifest(
        out_dir=out_dir,
        spec=spec,
        inputs=inputs,
        configs=configs,
        trials=trials,
        executed=executed,
        skipped=skipped,
        failed=failed,
        started_at=started_at,
    )

    return SweepSummary(
        experiment_id=spec.experiment_id,
        app_commit=app_commit(),
        results_path=results_path,
        manifest_path=manifest_path,
        planned=len(trials),
        executed=executed,
        skipped=skipped,
        failed=failed,
        usage=_merge_totals(totals),
    )


def _merge_totals(totals: list[UsageTotals]) -> UsageTotals:
    merged = UsageTotals()
    for item in totals:
        merged.calls += item.calls
        merged.calls_missing_usage += item.calls_missing_usage
        merged.input_tokens += item.input_tokens
        merged.output_tokens += item.output_tokens
        merged.total_tokens += item.total_tokens
        merged.cached_input_tokens += item.cached_input_tokens
        merged.reasoning_tokens += item.reasoning_tokens
    return merged
