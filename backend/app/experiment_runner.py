"""Sweep runner: turns a declarative spec into a persisted result set.

Each trial is checkpointed as an atomic JSON file and exported to ``results.jsonl``
in deterministic order. A trial that raised is recorded with its error, and each
phase gets its own trace context so tokens are attributable per phase. Run it with
``make experiment SPEC=<path> OUT=<dir>``.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, nullcontext
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
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
TRIALS_DIRNAME = "trials"

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
    dataset_version: str | None = None
    inputs: list[str] = Field(default_factory=list)
    # mirrored .txt tree, joined to paths below the input's `variants/` segment
    description_root: str | None = None
    # applied after `inputs` expands, so the manifest records what was excluded
    exclude: list[str] = Field(default_factory=list)
    base: dict[str, Any] = Field(default_factory=dict)
    axes: dict[str, list[Any]] = Field(default_factory=dict)
    configs: list[dict[str, Any]] = Field(default_factory=list)
    # validation-only sweeps skip the otherwise automatic repair/revalidation phases
    run_repair: bool = True
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


def resolve_descriptions(
    spec: SweepSpec,
    inputs: list[Path],
    root: Path,
) -> dict[Path, Path]:
    """Resolve one explicit reference description for every dataset variant."""
    if spec.description_root is None:
        return {}

    description_root = Path(spec.description_root)
    if not description_root.is_absolute():
        description_root = root / description_root

    descriptions: dict[Path, Path] = {}
    for input_path in inputs:
        marker_positions = [
            (index, part)
            for index, part in enumerate(input_path.parts)
            if part in {"variants", "seeds"}
        ]
        if not marker_positions:
            raise ValueError(
                "input has no 'variants' or 'seeds' path segment for description matching: "
                f"{input_path}"
            )
        marker_index, marker = marker_positions[-1]
        relative_start = (
            marker_index + 1 if description_root.name == marker else marker_index
        )
        relative = Path(*input_path.parts[relative_start:]).with_suffix(".txt")
        description = description_root / relative
        if not description.is_file():
            raise FileNotFoundError(
                f"description not found for {input_path}: {description}"
            )
        descriptions[input_path] = description
    return descriptions


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
    description_path: str | None = None
    run_repair: bool = True
    repeat: int
    config: ExperimentConfig


def trial_id(
    experiment_id: str,
    input_path: Path,
    config: ExperimentConfig,
    repeat: int,
    description_path: Path | None = None,
    run_repair: bool = True,
) -> str:
    """a stable identity for resumption

    Derived from the trial's inputs, not its position, so reordering the spec keeps
    results on disk valid. The app commit is excluded, so a rebuild does not re-run.
    """
    parts = "|".join(
        [
            experiment_id,
            str(input_path),
            str(description_path or ""),
            str(run_repair),
            canonical_config_json(config),
            str(repeat),
        ]
    )
    return hash_bytes(parts.encode("utf-8"))


def build_trials(
    spec: SweepSpec,
    inputs: list[Path],
    configs: list[ExperimentConfig],
    descriptions: dict[Path, Path] | None = None,
) -> list[Trial]:
    trials: list[Trial] = []
    matched_descriptions = descriptions or {}
    for input_path in inputs:
        description_path = matched_descriptions.get(input_path)
        for config in configs:
            for repeat in range(spec.repeats):
                trials.append(
                    Trial(
                        trial_id=trial_id(
                            spec.experiment_id,
                            input_path,
                            config,
                            repeat,
                            description_path,
                            spec.run_repair,
                        ),
                        experiment_id=spec.experiment_id,
                        input_path=str(input_path),
                        description_path=(
                            str(description_path) if description_path else None
                        ),
                        run_repair=spec.run_repair,
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
    provider_version: str | None = None
    model: str
    reasoning_effort: str | None = None
    effective_reasoning_effort: str | None = None
    max_tokens: int
    effective_max_tokens: int | None = None
    temperature: float | None = None
    seed: int | None = None
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
    description_path: str | None = None
    description_hash: str | None = None
    description_chars: int = 0
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
        provider_version=trace.provider_version,
        model=trace.model,
        reasoning_effort=trace.reasoning_effort,
        effective_reasoning_effort=trace.effective_reasoning_effort,
        max_tokens=trace.max_tokens,
        effective_max_tokens=trace.effective_max_tokens,
        temperature=trace.temperature,
        seed=trace.seed,
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
    reference_description: str | None = None

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
        if trial.description_path is not None:
            reference_description = Path(trial.description_path).read_text(
                encoding="utf-8"
            )
        validation_description = (
            reference_description if config.include_reference_description else None
        )
        diagram, unsupported = parse_bpmn_bytes_with_diagnostics(raw)
        final_diagram = diagram

        traces: list[LlmTrace] = []
        phase_started = time.perf_counter()
        async with _phase(traces):
            validation = await validate_diagram(
                diagram,
                config=config,
                reference_description=validation_description,
            )
        phases["validate"] = _phase_record(
            traces, _elapsed_ms(phase_started), keep_payloads
        )
        all_traces.extend(traces)

        issues = validation.issues + validation.semantic_issues
        pre_record = _validation_record(issues, validation.is_valid)

        if issues and trial.run_repair:
            traces = []
            phase_started = time.perf_counter()
            async with _phase(traces):
                repair = await dispatch_repair(
                    diagram,
                    issues=issues,
                    config=config,
                    reference_description=validation_description,
                )
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
                post = await validate_diagram(
                    final_diagram,
                    config=config,
                    reference_description=validation_description,
                )
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
        description_path=trial.description_path,
        description_hash=(
            hash_bytes(reference_description.encode("utf-8"))
            if reference_description is not None
            else None
        ),
        description_chars=len(reference_description or ""),
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
                **(
                    {"repair": repair_prompt_path(config)}
                    if trial.run_repair
                    else {}
                ),
                **(
                    {"validate": validate_prompt_path(config)}
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


def _jsonl_records(results_path: Path) -> list[dict[str, Any]]:
    """Read valid objects from a legacy or exported JSONL file."""
    if not results_path.is_file():
        return []
    records: list[dict[str, Any]] = []
    with results_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and isinstance(record.get("trial_id"), str):
                records.append(record)
    return records


def _trial_records_dir(out_dir: Path) -> Path:
    return out_dir / TRIALS_DIRNAME


def _trial_record_path(out_dir: Path, trial_id_value: str) -> Path:
    return _trial_records_dir(out_dir) / f"{trial_id_value}.json"


def _atomic_write_text(path: Path, content: str) -> None:
    """Replace one file atomically after its contents reach the filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_trial_record(out_dir: Path, record: TrialRecord) -> Path:
    """Persist one independently recoverable trial checkpoint."""
    path = _trial_record_path(out_dir, record.trial_id)
    _atomic_write_text(
        path,
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n",
    )
    return path


def _stored_trial_records(out_dir: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    directory = _trial_records_dir(out_dir)
    if not directory.is_dir():
        return records
    for path in directory.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        trial_id_value = record.get("trial_id") if isinstance(record, dict) else None
        if isinstance(trial_id_value, str) and path.stem == trial_id_value:
            records[trial_id_value] = record
    return records


def migrate_legacy_results(out_dir: Path) -> int:
    """Import valid records from the old append-only JSONL checkpoint format."""
    migrated = 0
    for record in _jsonl_records(out_dir / RESULTS_FILENAME):
        path = _trial_record_path(out_dir, record["trial_id"])
        if path.exists():
            continue
        _atomic_write_text(path, json.dumps(record, ensure_ascii=False) + "\n")
        migrated += 1
    return migrated


def completed_trial_ids(location: Path) -> set[str]:
    """Return trial ids from an output directory or a legacy JSONL file."""
    if location.is_dir():
        return set(_stored_trial_records(location))
    return {record["trial_id"] for record in _jsonl_records(location)}


def clear_trial_records(out_dir: Path) -> None:
    """Remove only checkpoints owned by one explicit fresh experiment run."""
    directory = _trial_records_dir(out_dir)
    if not directory.is_dir():
        return
    for path in directory.glob("*.json"):
        path.unlink()


def export_results_jsonl(out_dir: Path, trials: list[Trial]) -> Path:
    """Build the compatibility JSONL export in deterministic trial order."""
    stored = _stored_trial_records(out_dir)
    content = "".join(
        json.dumps(stored[trial.trial_id], ensure_ascii=False) + "\n"
        for trial in trials
        if trial.trial_id in stored
    )
    path = out_dir / RESULTS_FILENAME
    _atomic_write_text(path, content)
    return path


def write_manifest(
    out_dir: Path,
    spec: SweepSpec,
    inputs: list[Path],
    descriptions: dict[Path, Path],
    configs: list[ExperimentConfig],
    trials: list[Trial],
    executed: int,
    skipped: int,
    failed: int,
    started_at: datetime,
    concurrency: int,
) -> Path:
    invocation_finished_at = datetime.now(UTC)
    stored = _stored_trial_records(out_dir)
    completed_records = [
        stored[trial.trial_id] for trial in trials if trial.trial_id in stored
    ]
    record_windows: list[tuple[datetime, datetime]] = []
    for record in completed_records:
        try:
            record_started = datetime.fromisoformat(str(record["started_at"]))
            duration = timedelta(milliseconds=max(0, int(record["duration_ms"])))
        except (KeyError, TypeError, ValueError):
            continue
        record_windows.append((record_started, record_started + duration))

    experiment_started_at = (
        min(window[0] for window in record_windows) if record_windows else started_at
    )
    experiment_finished_at = (
        max(window[1] for window in record_windows)
        if record_windows
        else invocation_finished_at
    )
    failed_total = sum(bool(record.get("error")) for record in completed_records)
    manifest = {
        "experiment_id": spec.experiment_id,
        "dataset_version": spec.dataset_version,
        "app_commit": app_commit(),
        "started_at": experiment_started_at.isoformat(),
        "finished_at": experiment_finished_at.isoformat(),
        "spec": spec.model_dump(mode="json"),
        "inputs": [str(path) for path in inputs],
        "input_hashes": {
            str(path): hash_bytes(path.read_bytes()) for path in inputs
        },
        "description_hashes": {
            str(path): hash_bytes(path.read_bytes())
            for path in descriptions.values()
        },
        "config_count": len(configs),
        "config_hashes": [config_hash(config) for config in configs],
        "trial_count": len(trials),
        "completed": len(completed_records),
        "executed": len(completed_records),
        "executed_this_run": executed,
        "skipped_already_done": skipped,
        "failed": failed_total,
        "failed_this_run": failed,
        "concurrency": concurrency,
        "last_invocation": {
            "started_at": started_at.isoformat(),
            "finished_at": invocation_finished_at.isoformat(),
            "executed": executed,
            "skipped_already_done": skipped,
            "failed": failed,
            "concurrency": concurrency,
        },
        "trial_records_dir": TRIALS_DIRNAME,
        "results_file": RESULTS_FILENAME,
    }
    path = out_dir / MANIFEST_FILENAME
    _atomic_write_text(path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
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
    concurrency: int = 1,
    on_trial: Any = None,
) -> SweepSummary:
    """Run trials with bounded concurrency and atomic per-trial checkpoints."""
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    inputs = resolve_inputs(spec, root)
    descriptions = resolve_descriptions(spec, inputs, root)
    configs = expand_configs(spec)
    trials = build_trials(spec, inputs, configs, descriptions)

    out_dir.mkdir(parents=True, exist_ok=True)
    if resume:
        migrate_legacy_results(out_dir)
        already_done = completed_trial_ids(out_dir)
    else:
        clear_trial_records(out_dir)
        already_done = set()

    pending = [trial for trial in trials if trial.trial_id not in already_done]
    skipped = len(trials) - len(pending)
    if limit is not None:
        pending = pending[:limit]

    started_at = datetime.now(UTC)
    executed = 0
    failed = 0
    totals: list[UsageTotals] = []

    async with _mocked_providers() if mock else nullcontext():
        semaphore = asyncio.Semaphore(concurrency)

        async def execute_one(trial: Trial) -> TrialRecord:
            async with semaphore:
                return await run_trial(trial, keep_payloads=keep_payloads)

        tasks = [asyncio.create_task(execute_one(trial)) for trial in pending]
        try:
            for completed in asyncio.as_completed(tasks):
                record = await completed
                write_trial_record(out_dir, record)
                executed += 1
                if record.error:
                    failed += 1
                totals.append(record.usage)
                if on_trial is not None:
                    on_trial(executed, len(pending), record)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    results_path = export_results_jsonl(out_dir, trials)

    manifest_path = write_manifest(
        out_dir=out_dir,
        spec=spec,
        inputs=inputs,
        descriptions=descriptions,
        configs=configs,
        trials=trials,
        executed=executed,
        skipped=skipped,
        failed=failed,
        started_at=started_at,
        concurrency=concurrency,
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
