"""Prepare reproducible validation experiment specifications.

The command reads the frozen ground truth tree and writes ordinary YAML
understood by ``app.experiment_runner``. All eligible source seeds participate
in each applicable experiment; E3/E4 alone use their declared small sample.

Example (from the repository root; no model is contacted)::

    source .venv/bin/activate
    PYTHONPATH=backend python experiments/prepare_experiments.py \
        --output-dir /tmp/bpmn-specs \
        --gpt-model gpt-5.6 --claude-model claude-sonnet-4-5 \
        --winner-provider codex_cli --winner-model gpt-5.6

The generated files can be checked without running a provider::

    PYTHONPATH=backend python -c \
        'from pathlib import Path; from app.experiment_runner import load_spec, expand_configs; \
         [print(p, len(expand_configs(load_spec(p)))) for p in Path("/tmp/bpmn-specs").glob("*.yaml")]'

This module only prepares data and configuration.  It never calls an LLM.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import yaml


DATASET_VERSION = "v1.0"
DEFAULT_DATASET_ROOT = Path("data/eval/v1.0")
DEFAULT_REPEATS = 1
IR_FORMATS = ["pydantic", "pydantic_json", "yaml", "mermaid", "compact_json"]
PIPELINES = {
    "deterministic": ({"t1": True, "t2": True, "t3": False}, "semantic"),
    "llm": ({"t1": False, "t2": False, "t3": True}, "holistic"),
    "full": ({"t1": True, "t2": True, "t3": True}, "holistic"),
}


def read_ground_truth(dataset_root: Path) -> list[dict[str, Any]]:
    """Read and minimally validate every ground-truth JSON file in stable order."""

    ground_truth_root = dataset_root / "ground_truth"
    records: list[dict[str, Any]] = []
    for path in sorted(ground_truth_root.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"ground truth must be an object: {path}")
        for field in ("variant_id", "seed", "class"):
            if not isinstance(data.get(field), str) or not data[field]:
                raise ValueError(f"ground truth field {field!r} is missing: {path}")
        records.append(data)
    if not records:
        raise ValueError(f"no ground-truth JSON files found below {ground_truth_root}")
    return records


def _variant_path(dataset_root: Path, variant_id: str) -> str:
    return str(dataset_root / "variants" / f"{variant_id}.bpmn")


def _seed_path(dataset_root: Path, seed: str) -> str:
    return str(dataset_root / "seeds" / f"{seed}.bpmn")


def _description_root(dataset_root: Path) -> str:
    return str(dataset_root / "descriptions")


def _multiplicity(record: dict[str, Any]) -> int:
    value = record.get("multiplicity")
    if isinstance(value, int):
        return value
    return 1 if str(record["variant_id"]).startswith("single/") else 2


def _inputs(
    records: Iterable[dict[str, Any]],
    *,
    dataset_root: Path,
    seeds: set[str],
    classes: set[str] | None = None,
    multiplicities: set[int] | None = None,
) -> list[str]:
    """Build exact, stable variant paths, optionally followed by clean controls."""

    selected = [
        record
        for record in records
        if str(record["seed"]) in seeds
        and (classes is None or str(record["class"]) in classes)
        and (multiplicities is None or _multiplicity(record) in multiplicities)
    ]
    return sorted(
        {
            _variant_path(dataset_root, str(record["variant_id"]))
            for record in selected
        }
    )


def _with_clean_controls(
    variant_inputs: Sequence[str], *, dataset_root: Path, seeds: set[str]
) -> list[str]:
    return sorted(set(variant_inputs) | {_seed_path(dataset_root, seed) for seed in seeds})


def _model_base(provider: str, model: str, *, scope: str) -> dict[str, Any]:
    return {
        "model_tier": "custom",
        "model_override": model,
        "provider_override": provider,
        "ir_format": "pydantic",
        "tiers_enabled": {"t1": False, "t2": False, "t3": True},
        "llm_validation_scope": scope,
    }


def _paired_model_configs(claude_model: str) -> list[dict[str, str]]:
    # SweepSpec always expands ``base`` once.  The base is the explicit Codex
    # configuration; this list adds the paired Claude configuration without a
    # duplicate first trial.
    return [
        {"provider_override": "claude_cli", "model_override": claude_model},
    ]


def _semantic_spec(
    *,
    experiment_id: str,
    inputs: list[str],
    description_root: str,
    base: dict[str, Any],
    axes: dict[str, list[Any]] | None = None,
    configs: list[dict[str, Any]] | None = None,
    repeats: int,
    notes: str,
) -> dict[str, Any]:
    """Create a semantic spec, including its mirrored descriptions only here."""

    spec: dict[str, Any] = {
        "experiment_id": experiment_id,
        "dataset_version": DATASET_VERSION,
        "inputs": inputs,
        "description_root": description_root,
        "base": base,
        "run_repair": False,
    }
    if axes:
        spec["axes"] = axes
    if configs:
        spec["configs"] = configs
    spec["repeats"] = repeats
    spec["notes"] = notes
    return spec


def _validator_spec(
    *,
    inputs: list[str],
    winner_provider: str,
    winner_model: str,
    repeats: int,
) -> dict[str, Any]:
    # As with model selection, the runner materializes base once.  Make that
    # materialized config the deterministic-only control and append only the
    # two requested holistic controls below.
    base = _model_base(winner_provider, winner_model, scope="semantic")
    base["tiers_enabled"] = {"t1": True, "t2": True, "t3": False}
    # These are explicit controls, not a boolean product with accidental empty
    # or semantic variants.  The scope is intentional on every config so the
    # record states the exact contract even when tier 3 is disabled.
    configs = [
        {
            "tiers_enabled": {"t1": False, "t2": False, "t3": True},
            "llm_validation_scope": "holistic",
        },
        {
            "tiers_enabled": {"t1": True, "t2": True, "t3": True},
            "llm_validation_scope": "holistic",
        },
    ]
    return {
        "experiment_id": "validator-contribution",
        "dataset_version": DATASET_VERSION,
        "inputs": inputs,
        "base": base,
        "configs": configs,
        "run_repair": False,
        "repeats": repeats,
        "notes": (
            "All single STRUCT/SOUND variants plus clean source-seed controls; "
            "deterministic-only, holistic LLM-only, and full validation."
        ),
    }


def _small_semantic_sample(
    records: Sequence[dict[str, Any]], *, dataset_root: Path, seeds: set[str]
) -> list[str]:
    """Select one stable single-defect case for each semantic operator."""

    selected: list[dict[str, Any]] = []
    for operator in [f"M{index:02d}" for index in range(1, 8)]:
        candidates = sorted(
            (
                record
                for record in records
                if str(record["seed"]) in seeds
                and str(record["class"]) == "SEM"
                and _multiplicity(record) == 1
                and operator in record.get("operators", [])
            ),
            key=lambda record: str(record["variant_id"]),
        )
        if candidates:
            selected.append(candidates[0])
    inputs = [
        _variant_path(dataset_root, str(record["variant_id"])) for record in selected
    ]
    sample_seeds = {str(record["seed"]) for record in selected}
    return _with_clean_controls(inputs, dataset_root=dataset_root, seeds=sample_seeds)


def _confirmatory_variants(
    records: Sequence[dict[str, Any]], *, dataset_root: Path, seeds: set[str]
) -> list[str]:
    """Keep every variant from every selected source seed."""

    return sorted(
        _variant_path(dataset_root, str(record["variant_id"]))
        for record in records
        if str(record["seed"]) in seeds
    )


def build_specs(
    *,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    gpt_model: str,
    claude_model: str,
    winner_provider: str,
    winner_model: str,
    winner_pipeline: str = "full",
    repeats: int = DEFAULT_REPEATS,
) -> dict[str, dict[str, Any]]:
    """Build plain-Python spec payloads without writing or running them."""

    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    if winner_pipeline not in PIPELINES:
        raise ValueError(f"unknown winner pipeline: {winner_pipeline}")
    records = read_ground_truth(dataset_root)
    all_seeds = {str(record["seed"]) for record in records}

    dev_semantic = _with_clean_controls(
        _inputs(
            records,
            dataset_root=dataset_root,
            seeds=all_seeds,
            classes={"SEM"},
            multiplicities={1},
        ),
        dataset_root=dataset_root,
        seeds=all_seeds,
    )
    dev_struct_sound = _inputs(
        records,
        dataset_root=dataset_root,
        seeds=all_seeds,
        classes={"STRUCT", "SOUND"},
        multiplicities={1},
    )
    validator_inputs = _with_clean_controls(
        dev_struct_sound, dataset_root=dataset_root, seeds=all_seeds
    )
    ablation_inputs = _small_semantic_sample(
        records, dataset_root=dataset_root, seeds=all_seeds
    )
    confirmatory_inputs = _with_clean_controls(
        _confirmatory_variants(
            records, dataset_root=dataset_root, seeds=all_seeds
        ),
        dataset_root=dataset_root,
        seeds=all_seeds,
    )
    confirmatory_tiers, confirmatory_scope = PIPELINES[winner_pipeline]

    return {
        "model-selection": _semantic_spec(
            experiment_id="model-selection",
            inputs=dev_semantic,
            description_root=_description_root(dataset_root),
            base=_model_base("codex_cli", gpt_model, scope="semantic"),
            configs=_paired_model_configs(claude_model),
            repeats=repeats,
            notes="All single SEM variants and clean controls; paired Codex CLI and Claude CLI model configurations.",
        ),
        "ir-selection": _semantic_spec(
            experiment_id="ir-selection",
            inputs=dev_semantic,
            description_root=_description_root(dataset_root),
            base=_model_base(winner_provider, winner_model, scope="semantic"),
            axes={"ir_format": list(IR_FORMATS)},
            repeats=repeats,
            notes="All single SEM variants and clean controls across all IRs.",
        ),
        "reasoning-ablation": _semantic_spec(
            experiment_id="reasoning-ablation",
            inputs=ablation_inputs,
            description_root=_description_root(dataset_root),
            base=_model_base(winner_provider, winner_model, scope="semantic"),
            axes={"reasoning_effort": ["low", "medium", "high"]},
            repeats=repeats,
            notes="One stable single-defect case per semantic operator, plus clean controls.",
        ),
        "description-ablation": _semantic_spec(
            experiment_id="description-ablation",
            inputs=ablation_inputs,
            description_root=_description_root(dataset_root),
            base={
                **_model_base(winner_provider, winner_model, scope="semantic"),
                "include_reference_description": False,
            },
            configs=[{"include_reference_description": True}],
            repeats=repeats,
            notes="Paired diagram-only and diagram-plus-description conditions.",
        ),
        "validator-contribution": _validator_spec(
            inputs=validator_inputs,
            winner_provider=winner_provider,
            winner_model=winner_model,
            repeats=repeats,
        ),
        "confirmatory-detection": _semantic_spec(
            experiment_id="confirmatory-detection",
            inputs=confirmatory_inputs,
            description_root=_description_root(dataset_root),
            base={
                **_model_base(
                    winner_provider, winner_model, scope=confirmatory_scope
                ),
                "tiers_enabled": confirmatory_tiers,
            },
            repeats=repeats,
            notes="All single, disjoint, interacting, and clean cases with the frozen winning configuration.",
        ),
    }


def write_specs(specs: dict[str, dict[str, Any]], output_dir: Path) -> list[Path]:
    """Write stable YAML files in name order and return their paths."""

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name in sorted(specs):
        path = output_dir / f"{name}.yaml"
        text = yaml.safe_dump(
            specs[name],
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            width=100,
        )
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths


def prepare_experiments(
    *,
    output_dir: Path,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    gpt_model: str,
    claude_model: str,
    winner_provider: str,
    winner_model: str,
    winner_pipeline: str = "full",
    repeats: int = DEFAULT_REPEATS,
) -> list[Path]:
    """Build and write specs; this is the small programmatic API for tests."""

    return write_specs(
        build_specs(
            dataset_root=dataset_root,
            gpt_model=gpt_model,
            claude_model=claude_model,
            winner_provider=winner_provider,
            winner_model=winner_model,
            winner_pipeline=winner_pipeline,
            repeats=repeats,
        ),
        output_dir,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--output-dir", "--out-dir", type=Path, required=True)
    parser.add_argument("--gpt-model", required=True)
    parser.add_argument("--claude-model", required=True)
    parser.add_argument("--winner-provider", required=True)
    parser.add_argument("--winner-model", required=True)
    parser.add_argument(
        "--winner-pipeline",
        choices=sorted(PIPELINES),
        default="full",
    )
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = prepare_experiments(
        output_dir=args.output_dir,
        dataset_root=args.dataset_root,
        gpt_model=args.gpt_model,
        claude_model=args.claude_model,
        winner_provider=args.winner_provider,
        winner_model=args.winner_model,
        winner_pipeline=args.winner_pipeline,
        repeats=args.repeats,
    )
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
