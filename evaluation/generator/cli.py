"""CLI for the dataset generator. Run via `make eligibility-probe`."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from evaluation.generator.build import (
	DEFAULT_RANDOM_SEED,
	audit_dataset,
	build_dataset,
	census_soundness_sites,
	refresh_dataset_manifest,
)
from evaluation.generator.exclusions import render_exclusions, render_seed_list
from evaluation.generator.probe import Verdict, run_probe

app = typer.Typer(
	add_completion=False,
	help="Build the evaluation dataset from a source corpus.",
)

_PMO_DIR = Path("data/pmo-dataset")


@app.callback()
def main() -> None:
	"""keeps subcommand names stable while the generator grows past one stage"""


@app.command("probe")
def probe_command(
	source: Path = typer.Option(
		_PMO_DIR / "bpmn",
		"--source",
		exists=True,
		file_okay=False,
		help="directory of source .bpmn models",
	),
	descriptions: Path = typer.Option(
		_PMO_DIR / "descriptions",
		"--descriptions",
		help="directory of matching .txt descriptions",
	),
	out: Path | None = typer.Option(
		None,
		"--out",
		help="write probe.json, EXCLUSIONS.md and seeds.txt here",
	),
) -> None:
	"""Decide which source models qualify as seeds (METHODOLOGY §2)."""
	report = run_probe(source, descriptions if descriptions.exists() else None)

	counts = report.counts()
	typer.echo(f"{len(report.seeds)}/{len(report.records)} models passed the seed gate")
	for verdict in Verdict:
		if counts.get(verdict.value):
			typer.echo(f"  {verdict.value:<20} {counts[verdict.value]}")

	if out is None:
		return

	out.mkdir(parents=True, exist_ok=True)
	(out / "probe.json").write_text(
		json.dumps(report.model_dump(mode="json"), indent=2) + "\n"
	)
	(out / "EXCLUSIONS.md").write_text(render_exclusions(report))
	(out / "seeds.txt").write_text(render_seed_list(report))
	typer.echo(f"wrote probe.json, EXCLUSIONS.md and seeds.txt to {out}")


@app.command("build")
def build_command(
	source: Path = typer.Option(
		_PMO_DIR / "bpmn",
		"--source",
		exists=True,
		file_okay=False,
	),
	descriptions: Path = typer.Option(
		_PMO_DIR / "descriptions",
		"--descriptions",
		exists=True,
		file_okay=False,
	),
	probe: Path = typer.Option(
		Path("data/eval/v1.0/probe.json"),
		"--probe",
		exists=True,
		dir_okay=False,
	),
	out: Path = typer.Option(..., "--out", help="dataset version directory"),
	dataset_version: str = typer.Option("v1.0", "--dataset-version"),
	random_seed: int = typer.Option(DEFAULT_RANDOM_SEED, "--random-seed"),
) -> None:
	"""Snapshot eligible seeds and generate deterministic dataset variants."""
	manifest = build_dataset(
		source_dir=source,
		description_dir=descriptions,
		probe_path=probe,
		out=out,
		dataset_version=dataset_version,
		random_seed=random_seed,
	)
	typer.echo(
		f"wrote {manifest.counts['seeds']} seeds and "
		f"{manifest.counts['variants']} variants to {out}"
	)


@app.command("census-soundness")
def census_soundness_command(
	source: Path = typer.Option(
		_PMO_DIR / "bpmn",
		"--source",
		exists=True,
		file_okay=False,
	),
	probe: Path = typer.Option(
		Path("data/eval/v1.0/probe.json"),
		"--probe",
		exists=True,
		dir_okay=False,
	),
	random_seed: int = typer.Option(DEFAULT_RANDOM_SEED, "--random-seed"),
	out: Path | None = typer.Option(None, "--out"),
) -> None:
	"""Find reproducible F01-F04 injection sites without emitting variants."""
	report = census_soundness_sites(
		source_dir=source,
		probe_path=probe,
		random_seed=random_seed,
	)
	for name, count in report["counts"].items():
		typer.echo(f"{name:<16} {count}")
	if out is not None:
		out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
		typer.echo(f"wrote soundness census to {out}")


@app.command("audit")
def audit_command(
	dataset: Path = typer.Option(
		...,
		"--dataset",
		exists=True,
		file_okay=False,
	),
) -> None:
	"""Verify a generated dataset without modifying it."""
	counts = audit_dataset(dataset)
	typer.echo(
		f"verified {counts['seeds']} seeds and {counts['variants']} variants"
	)


@app.command("refresh-manifest")
def refresh_manifest_command(
	dataset: Path = typer.Option(
		...,
		"--dataset",
		exists=True,
		file_okay=False,
	),
) -> None:
	"""Refresh hashes and counts after an explicit human curation change."""
	manifest = refresh_dataset_manifest(dataset)
	typer.echo(
		f"refreshed manifest for {manifest.counts['seeds']} seeds and "
		f"{manifest.counts['variants']} variants"
	)


if __name__ == "__main__":
	app()
