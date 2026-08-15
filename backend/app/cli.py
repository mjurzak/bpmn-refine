"""Typer CLI for backend-local validation and experimentation workflows."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import typer

from app.llm.router import TaskType, resolve_model, resolve_provider
from app.services.chat import ChatMessage, chat_diagram, chat_prompt_name
from app.services.diagrams import export_bpmn_xml, load_bpmn_file
from app.services.repair import repair_diagram, repair_prompt_name
from app.services.validation import validate_diagram, validate_prompt_name
from app.validation.rules import ValidationIssue

app = typer.Typer(
    add_completion=False,
    help="CLI for BPMN validation and round-trip experimentation.",
)


@app.command("validate")
def validate_command(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    semantic: bool = typer.Option(
        False, "--semantic", help="run the optional LLM semantic pass"
    ),
    json_output: bool = typer.Option(False, "--json", help="print the result as JSON"),
    include_metadata: bool = typer.Option(
        False, "--include-metadata", help="include experiment metadata in the output"
    ),
) -> None:
    """Validate a BPMN diagram file."""
    started_at = time.perf_counter()
    try:
        diagram = load_bpmn_file(file)
        result = asyncio.run(validate_diagram(diagram, include_semantic=semantic))
    except Exception as exc:
        typer.secho(f"Failed to validate {file}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    payload: dict[str, Any] = {
        "file": str(file),
        **result.model_dump(mode="json"),
    }
    if include_metadata or json_output:
        payload["metadata"] = _build_metadata(
            command="validate",
            dataset_path=file,
            started_at=started_at,
            task=TaskType.SEMANTIC_VALIDATION if semantic else None,
            prompt_name=validate_prompt_name() if semantic else None,
            extra={"semantic": semantic},
        )
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        _print_issue_group(result.issues)
        _print_issue_group(result.semantic_issues, prefix="semantic")
        typer.echo("VALID" if result.is_valid else "INVALID")
        if include_metadata:
            typer.echo(_format_metadata_line(payload["metadata"]))

    if not result.is_valid:
        raise typer.Exit(code=1)


@app.command("roundtrip")
def roundtrip_command(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    out: Path | None = typer.Option(
        None, "--out", dir_okay=False, help="write the exported BPMN XML to a file"
    ),
    include_metadata: bool = typer.Option(
        False, "--include-metadata", help="print experiment metadata after the export"
    ),
) -> None:
    """Parse a BPMN file and export it again."""
    started_at = time.perf_counter()
    try:
        diagram = load_bpmn_file(file)
        xml = export_bpmn_xml(diagram)
    except Exception as exc:
        typer.secho(
            f"Failed to round-trip {file}: {exc}", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2) from exc

    if out is None:
        typer.echo(xml)
        if include_metadata:
            typer.echo(
                _format_metadata_line(
                    _build_metadata(
                        command="roundtrip",
                        dataset_path=file,
                        started_at=started_at,
                    )
                )
            )
        return

    out.write_text(xml, encoding="utf-8")
    typer.echo(f"Wrote round-tripped BPMN to {out}")
    if include_metadata:
        typer.echo(
            _format_metadata_line(
                _build_metadata(
                    command="roundtrip",
                    dataset_path=file,
                    started_at=started_at,
                )
            )
        )


@app.command("chat")
def chat_command(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    message: str = typer.Option(
        ..., "--message", help="single user message for the refinement turn"
    ),
    out: Path | None = typer.Option(
        None, "--out", dir_okay=False, help="write the updated BPMN XML to a file"
    ),
    json_output: bool = typer.Option(False, "--json", help="print the result as JSON"),
    issues_from_validate: bool = typer.Option(
        True,
        "--issues-from-validate/--no-issues-from-validate",
        help="include deterministic validation issues in the chat context",
    ),
    semantic: bool = typer.Option(
        False,
        "--semantic",
        help="include semantic issues when validation context is enabled",
    ),
    include_metadata: bool = typer.Option(
        False, "--include-metadata", help="include experiment metadata in the output"
    ),
    session_id: str | None = typer.Option(
        None, "--session-id", help="reuse an existing history session"
    ),
) -> None:
    """Run one chat refinement turn against a BPMN diagram."""
    started_at = time.perf_counter()
    try:
        diagram = load_bpmn_file(file)
        issues: list[ValidationIssue] = []
        if issues_from_validate:
            validation_result = asyncio.run(
                validate_diagram(diagram, include_semantic=semantic)
            )
            issues = validation_result.issues + validation_result.semantic_issues

        result = asyncio.run(
            chat_diagram(
                messages=[ChatMessage(role="user", content=message)],
                diagram=diagram,
                issues=issues,
                session_id=session_id,
            )
        )
    except Exception as exc:
        typer.secho(f"Failed to refine {file}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    payload: dict[str, Any] = {
        "file": str(file),
        **result.model_dump(mode="json"),
    }
    if include_metadata or json_output:
        payload["metadata"] = _build_metadata(
            command="chat",
            dataset_path=file,
            started_at=started_at,
            task=TaskType.REFINEMENT,
            prompt_name=chat_prompt_name(),
            extra={
                "issues_from_validate": issues_from_validate,
                "semantic": semantic,
            },
        )
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(result.reply)
        if result.rev_id:
            typer.echo(f"Revision: {result.rev_id}")
        if result.session_id:
            typer.echo(f"Session: {result.session_id}")
        if include_metadata:
            typer.echo(_format_metadata_line(payload["metadata"]))

    if out is None:
        return

    if result.updated_diagram is None:
        typer.secho(
            "Chat reply did not include an updated diagram.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1)

    out.write_text(export_bpmn_xml(result.updated_diagram), encoding="utf-8")
    typer.echo(f"Wrote refined BPMN to {out}")


@app.command("repair")
def repair_command(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    out: Path | None = typer.Option(
        None, "--out", dir_okay=False, help="write the repaired BPMN XML to a file"
    ),
    json_output: bool = typer.Option(False, "--json", help="print the result as JSON"),
    semantic: bool = typer.Option(
        False, "--semantic", help="include semantic issues in the repair input"
    ),
    include_metadata: bool = typer.Option(
        False, "--include-metadata", help="include experiment metadata in the output"
    ),
    session_id: str | None = typer.Option(
        None, "--session-id", help="reuse an existing history session"
    ),
) -> None:
    """Repair a BPMN diagram using the existing repair prompt."""
    started_at = time.perf_counter()
    try:
        diagram = load_bpmn_file(file)
        pre_validation = asyncio.run(
            validate_diagram(diagram, include_semantic=semantic)
        )
        issues = pre_validation.issues + pre_validation.semantic_issues
        if not issues:
            payload: dict[str, Any] = {
                "file": str(file),
                "repaired": False,
                "message": "No validation issues found; nothing to repair.",
            }
            if include_metadata or json_output:
                payload["metadata"] = _build_metadata(
                    command="repair",
                    dataset_path=file,
                    started_at=started_at,
                    task=TaskType.REPAIR,
                    prompt_name=repair_prompt_name(),
                    extra={"semantic": semantic, "issue_count": 0},
                )
            if json_output:
                typer.echo(json.dumps(payload, indent=2))
            else:
                typer.echo(payload["message"])
                if include_metadata:
                    typer.echo(_format_metadata_line(payload["metadata"]))
            return

        result = asyncio.run(
            repair_diagram(diagram, issues=issues, session_id=session_id)
        )
        post_validation = asyncio.run(
            validate_diagram(result.repaired_diagram, include_semantic=semantic)
        )
    except Exception as exc:
        typer.secho(f"Failed to repair {file}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    payload: dict[str, Any] = {
        "file": str(file),
        "repaired": True,
        "unresolved": [item.model_dump(mode="json") for item in result.unresolved],
        "rev_id": result.rev_id,
        "session_id": result.session_id,
        "post_validation": post_validation.model_dump(mode="json"),
    }
    if include_metadata or json_output:
        payload["metadata"] = _build_metadata(
            command="repair",
            dataset_path=file,
            started_at=started_at,
            task=TaskType.REPAIR,
            prompt_name=repair_prompt_name(),
            extra={"semantic": semantic, "issue_count": len(issues)},
        )

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo("Repair completed.")
        typer.echo(
            "Post-validation: VALID"
            if post_validation.is_valid
            else "Post-validation: INVALID"
        )
        for item in result.unresolved:
            typer.echo(f"UNRESOLVED {item.rule_id or '-'} {item.reason}")
        if result.rev_id:
            typer.echo(f"Revision: {result.rev_id}")
        if result.session_id:
            typer.echo(f"Session: {result.session_id}")
        if include_metadata:
            typer.echo(_format_metadata_line(payload["metadata"]))

    if out is not None:
        out.write_text(export_bpmn_xml(result.repaired_diagram), encoding="utf-8")
        typer.echo(f"Wrote repaired BPMN to {out}")

    if not post_validation.is_valid:
        raise typer.Exit(code=1)


@app.command("dry-run")
def dry_run_command(
    out: Path = typer.Option(
        ...,
        "--out",
        dir_okay=False,
        help="where to write the self-contained run record",
    ),
    inputs: list[Path] = typer.Option(
        None,
        "--input",
        exists=True,
        dir_okay=False,
        readable=True,
        help="BPMN file to run the matrix against; repeatable",
    ),
) -> None:
    """Exercise every ablation control with mocked providers, writing a record.

    Makes no paid API call. Confirms that each control reaches the executed path
    before any experiment is run against it.
    """
    from app.dry_run import DEFAULT_INPUTS, execute

    record = asyncio.run(execute(inputs or list(DEFAULT_INPUTS)))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")

    typer.echo(f"Wrote {record['case_count']} ablation cases to {out}")
    typer.echo(f"Implementation commit: {record['app_commit']}")
    typer.echo("No API calls were made; every model response was mocked.")


@app.command("run-experiment")
def run_experiment_command(
    spec: Path = typer.Option(
        ...,
        "--spec",
        exists=True,
        dir_okay=False,
        readable=True,
        help="sweep specification (JSON or YAML)",
    ),
    out: Path = typer.Option(
        ..., "--out", file_okay=False, help="directory for results.jsonl and manifest"
    ),
    root: Path = typer.Option(
        Path("."), "--root", file_okay=False, help="base directory for spec inputs"
    ),
    mock: bool = typer.Option(
        False, "--mock", help="rehearse against canned responses; makes no API call"
    ),
    resume: bool = typer.Option(
        True,
        "--resume/--no-resume",
        help="skip trials with an atomic checkpoint already on disk",
    ),
    limit: int | None = typer.Option(
        None, "--limit", help="run at most this many pending trials"
    ),
    keep_payloads: bool = typer.Option(
        False, "--keep-payloads", help="record full prompts and responses per call"
    ),
    concurrency: int = typer.Option(
        1,
        "--concurrency",
        min=1,
        help="maximum trials executed concurrently",
    ),
) -> None:
    """Run an ablation sweep and persist one JSON record per trial.

    Each completed trial is checkpointed atomically, so an interrupted sweep
    resumes where it stopped. Rehearse a new spec with --mock before spending.
    """
    from app.experiment_runner import execute_sweep, load_spec

    loaded = load_spec(spec)

    def report(index: int, total: int, record) -> None:
        status = "FAIL" if record.error else "ok"
        typer.echo(
            f"[{index}/{total}] {status} {record.trial_id} "
            f"{Path(record.input_path).name} cfg={record.run.config_hash} "
            f"tokens={record.usage.total_tokens}"
        )
        if record.error:
            typer.secho(f"        {record.error}", fg=typer.colors.RED, err=True)

    summary = asyncio.run(
        execute_sweep(
            loaded,
            out_dir=out,
            root=root,
            resume=resume,
            mock=mock,
            limit=limit,
            keep_payloads=keep_payloads,
            concurrency=concurrency,
            on_trial=report,
        )
    )

    typer.echo("")
    typer.echo(f"Experiment:   {summary.experiment_id}")
    typer.echo(f"Commit:       {summary.app_commit}")
    typer.echo(
        f"Trials:       {summary.executed} executed, "
        f"{summary.skipped} already done, {summary.failed} failed "
        f"({summary.planned} planned)"
    )
    typer.echo(
        f"Tokens:       {summary.usage.total_tokens} "
        f"(in {summary.usage.input_tokens} / out {summary.usage.output_tokens})"
    )
    if not summary.usage.complete:
        typer.secho(
            f"        {summary.usage.calls_missing_usage} call(s) reported no usage; "
            "the token total is a lower bound.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    typer.echo(f"Results:      {summary.results_path}")
    typer.echo(f"Manifest:     {summary.manifest_path}")
    if mock:
        typer.echo("Rehearsal only; every model response was mocked.")

    if summary.failed:
        raise typer.Exit(code=1)


@app.command("batch-validate")
def batch_validate_command(
    path: Path = typer.Argument(..., exists=True, readable=True),
    pattern: str = typer.Option("*.bpmn", "--pattern", help="file glob to validate"),
    recursive: bool = typer.Option(
        False, "--recursive", help="search recursively under the given path"
    ),
    semantic: bool = typer.Option(
        False, "--semantic", help="run the optional LLM semantic pass"
    ),
    out: Path | None = typer.Option(
        None, "--out", dir_okay=False, help="write one JSON object per line to a file"
    ),
    output_format: str = typer.Option(
        "jsonl",
        "--format",
        help="output format: json, jsonl, or text",
        case_sensitive=False,
    ),
    include_metadata: bool = typer.Option(
        True,
        "--include-metadata/--no-include-metadata",
        help="include experiment metadata per file",
    ),
    fail_on_error: bool = typer.Option(
        False, "--fail-on-error", help="exit non-zero if any diagram is invalid"
    ),
) -> None:
    """Validate many BPMN files for testing and experiments."""
    output_format = output_format.lower()
    if output_format not in {"json", "jsonl", "text"}:
        typer.secho(
            "--format must be one of: json, jsonl, text",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    files = _collect_bpmn_files(path, pattern, recursive)
    if not files:
        typer.secho(
            "No BPMN files matched the requested path and pattern.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=2)

    results: list[dict] = []
    invalid_count = 0
    for file in files:
        started_at = time.perf_counter()
        try:
            diagram = load_bpmn_file(file)
            result = asyncio.run(validate_diagram(diagram, include_semantic=semantic))
            payload: dict[str, Any] = {
                "file": str(file),
                **result.model_dump(mode="json"),
            }
        except Exception as exc:
            payload = {
                "file": str(file),
                "is_valid": False,
                "issues": [],
                "semantic_issues": [],
                "error": str(exc),
            }

        if include_metadata:
            payload["metadata"] = _build_metadata(
                command="batch-validate",
                dataset_path=file,
                started_at=started_at,
                task=TaskType.SEMANTIC_VALIDATION if semantic else None,
                prompt_name=validate_prompt_name() if semantic else None,
                extra={"semantic": semantic, "batch_root": str(path)},
            )

        if not payload["is_valid"]:
            invalid_count += 1
        results.append(payload)

    rendered = _render_batch_output(results, output_format)
    if out is not None:
        out.write_text(rendered, encoding="utf-8")
        typer.echo(f"Wrote batch validation results to {out}")
    else:
        typer.echo(rendered, nl=False)

    typer.echo(f"Validated {len(results)} file(s); invalid: {invalid_count}")

    if fail_on_error and invalid_count:
        raise typer.Exit(code=1)


def _print_issue_group(
    issues: list[ValidationIssue], prefix: str | None = None
) -> None:
    for issue in issues:
        label = issue.severity.upper()
        if prefix:
            label = f"{prefix.upper()} {label}"
        typer.echo(f"{label} {issue.rule_id} {issue.message}")


def _collect_bpmn_files(path: Path, pattern: str, recursive: bool) -> list[Path]:
    if path.is_file():
        return [path]

    iterator = path.rglob(pattern) if recursive else path.glob(pattern)
    return sorted(file for file in iterator if file.is_file())


def _build_metadata(
    command: str,
    dataset_path: Path,
    started_at: float,
    task: TaskType | None = None,
    prompt_name: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "command": command,
        "dataset_path": str(dataset_path),
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
        "provider": resolve_provider(task) if task is not None else None,
        "model": resolve_model(task) if task is not None else None,
        "prompt_version": prompt_name,
    }
    if extra:
        metadata.update(extra)
    return metadata


def _format_metadata_line(metadata: dict[str, Any]) -> str:
    parts = [
        f"duration_ms={metadata['duration_ms']}",
        f"dataset_path={metadata['dataset_path']}",
    ]
    if metadata.get("provider"):
        parts.append(f"provider={metadata['provider']}")
    if metadata.get("model"):
        parts.append(f"model={metadata['model']}")
    if metadata.get("prompt_version"):
        parts.append(f"prompt_version={metadata['prompt_version']}")
    return "METADATA " + " ".join(parts)


def _render_batch_output(results: list[dict[str, Any]], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(results, indent=2) + "\n"
    if output_format == "jsonl":
        return "\n".join(json.dumps(result) for result in results) + "\n"

    lines: list[str] = []
    for result in results:
        status = "VALID" if result["is_valid"] else "INVALID"
        lines.append(f"{status} {result['file']}")
        if result.get("error"):
            lines.append(f"ERROR {result['error']}")
        for issue in result.get("issues", []):
            lines.append(
                f"ISSUE {issue['severity'].upper()} {issue['rule_id']} {issue['message']}"
            )
        for issue in result.get("semantic_issues", []):
            lines.append(
                f"SEMANTIC {issue['severity'].upper()} {issue['rule_id']} {issue['message']}"
            )
        if result.get("metadata"):
            lines.append(_format_metadata_line(result["metadata"]))
    return "\n".join(lines) + ("\n" if lines else "")


if __name__ == "__main__":
    app()
