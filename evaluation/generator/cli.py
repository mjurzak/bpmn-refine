"""CLI for the dataset generator. Run via `make eligibility-probe`."""

from __future__ import annotations

import json
from pathlib import Path

import typer

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


if __name__ == "__main__":
	app()
