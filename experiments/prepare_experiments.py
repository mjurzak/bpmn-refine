"""Prepare reproducible validation experiment specifications.

The command reads the frozen ground truth tree and writes ordinary YAML
understood by ``app.experiment_runner``. All eligible source seeds participate
in each applicable experiment; E3/E4 alone use their declared small sample.

Example (from the repository root; no model is contacted)::

    source .venv/bin/activate
    PYTHONPATH=backend python experiments/prepare_experiments.py \
        --output-dir /tmp/bpmn-specs \
        --gpt-model gpt-5.6-terra --claude-model claude-sonnet-5 \
        --winner-provider codex_cli --winner-model gpt-5.6-terra

The generated files can be checked without running a provider::

    PYTHONPATH=backend python -c \
        'from pathlib import Path; from app.experiment_runner import load_spec, expand_configs; \
         [print(p, len(expand_configs(load_spec(p)))) for p in Path("/tmp/bpmn-specs").glob("*.yaml")]'

This module only prepares data and configuration.  It never calls an LLM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import yaml


DATASET_VERSION = "v1.0"
DEFAULT_DATASET_ROOT = Path("data/eval/v1.0")
DEFAULT_REPEATS = 1
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_SEMANTIC_PANEL_SIZE = 30
SEMANTIC_PANEL_QUOTAS = {
    30: {"clean": 6, "single_per_operator": 3, "disjoint": 3},
    100: {"clean": 20, "single_per_operator": 8, "disjoint": 24},
}
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


def _with_clean_controls(
    variant_inputs: Sequence[str], *, dataset_root: Path, seeds: set[str]
) -> list[str]:
    return sorted(set(variant_inputs) | {_seed_path(dataset_root, seed) for seed in seeds})


def _model_base(
    provider: str,
    model: str,
    *,
    scope: str,
    reasoning_effort: str | None = DEFAULT_REASONING_EFFORT,
    include_semantic_projection: bool = False,
) -> dict[str, Any]:
    base = {
        "model_tier": "custom",
        "model_override": model,
        "provider_override": provider,
        "ir_format": "pydantic",
        "tiers_enabled": {"t1": False, "t2": False, "t3": True},
        "llm_validation_scope": scope,
        "include_semantic_projection": include_semantic_projection,
    }
    if reasoning_effort is not None:
        base["reasoning_effort"] = reasoning_effort
    return base


def _stable_key(value: str, *, salt: str) -> str:
    return hashlib.sha256(f"{salt}|{value}".encode()).hexdigest()


def _stable_records(
    records: Iterable[dict[str, Any]], *, salt: str
) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda record: _stable_key(str(record["variant_id"]), salt=salt),
    )


def _balanced_pairs(
    records: Iterable[dict[str, Any]], *, count: int, salt: str
) -> list[dict[str, Any]]:
    """Choose paired defects while balancing operator and combination exposure."""
    remaining = _stable_records(records, salt=salt)
    selected: list[dict[str, Any]] = []
    operator_counts: dict[str, int] = {}
    combination_counts: dict[tuple[str, ...], int] = {}
    while remaining and len(selected) < count:
        def score(record: dict[str, Any]) -> tuple[int, int, str]:
            operators = tuple(sorted(str(value) for value in record.get("operators", [])))
            return (
                sum(operator_counts.get(operator, 0) for operator in operators),
                combination_counts.get(operators, 0),
                _stable_key(str(record["variant_id"]), salt=salt),
            )

        chosen = min(remaining, key=score)
        remaining.remove(chosen)
        selected.append(chosen)
        operators = tuple(sorted(str(value) for value in chosen.get("operators", [])))
        combination_counts[operators] = combination_counts.get(operators, 0) + 1
        for operator in operators:
            operator_counts[operator] = operator_counts.get(operator, 0) + 1
    return selected


def _semantic_panel(
    records: Sequence[dict[str, Any]],
    *,
    dataset_root: Path,
    size: int,
) -> list[str]:
    """Build a nested, balanced 30- or 100-case paid semantic panel."""
    if size not in SEMANTIC_PANEL_QUOTAS:
        raise ValueError(f"semantic panel size must be one of {sorted(SEMANTIC_PANEL_QUOTAS)}")
    quota = SEMANTIC_PANEL_QUOTAS[size]
    selected: list[dict[str, Any]] = []
    for operator in [f"M{index:02d}" for index in range(1, 8)]:
        candidates = _stable_records(
            (
                record
                for record in records
                if str(record["class"]) == "SEM"
                and _multiplicity(record) == 1
                and operator in record.get("operators", [])
            ),
            salt=f"semantic-single-{operator}",
        )
        selected.extend(candidates[: quota["single_per_operator"]])

    selected.extend(
        _balanced_pairs(
            (
                record
                for record in records
                if str(record["class"]) == "SEM"
                and _multiplicity(record) == 2
                and record.get("interaction") == "disjoint"
            ),
            count=quota["disjoint"],
            salt="semantic-disjoint",
        )
    )
    clean_seeds = sorted(
        {str(record["seed"]) for record in records},
        key=lambda seed: _stable_key(seed, salt="semantic-clean"),
    )[: quota["clean"]]
    variant_inputs = [
        _variant_path(dataset_root, str(record["variant_id"])) for record in selected
    ]
    return sorted(
        set(variant_inputs)
        | {_seed_path(dataset_root, seed) for seed in clean_seeds}
    )


def _confirmatory_panel(
    records: Sequence[dict[str, Any]], *, dataset_root: Path
) -> list[str]:
    """Build the fixed 100-case final panel across clean, single and paired cases."""
    groups = [
        (
            (r for r in records if str(r["class"]) == "SEM" and _multiplicity(r) == 1),
            28,
            "confirm-single-sem",
        ),
        (
            (
                r
                for r in records
                if str(r["class"]) in {"STRUCT", "SOUND"} and _multiplicity(r) == 1
            ),
            14,
            "confirm-single-formal",
        ),
        (
            (
                r
                for r in records
                if _multiplicity(r) == 2 and r.get("interaction") == "disjoint"
            ),
            24,
            "confirm-disjoint",
        ),
        (
            (
                r
                for r in records
                if _multiplicity(r) == 2 and r.get("interaction") == "interacting"
            ),
            14,
            "confirm-interacting",
        ),
    ]
    selected = [
        record
        for candidates, count, salt in groups
        for record in _balanced_pairs(candidates, count=count, salt=salt)
    ]
    clean_seeds = sorted(
        {str(record["seed"]) for record in records},
        key=lambda seed: _stable_key(seed, salt="confirm-clean"),
    )[:20]
    return sorted(
        {_variant_path(dataset_root, str(record["variant_id"])) for record in selected}
        | {_seed_path(dataset_root, seed) for seed in clean_seeds}
    )


def _validator_panel(
    records: Sequence[dict[str, Any]], *, dataset_root: Path
) -> list[str]:
    """Build a 100-case panel for comparing deterministic and LLM validators."""
    selected = _balanced_pairs(
        (
            record
            for record in records
            if str(record["class"]) in {"STRUCT", "SOUND"}
            and _multiplicity(record) == 1
        ),
        count=80,
        salt="validator-formal",
    )
    clean_seeds = sorted(
        {str(record["seed"]) for record in records},
        key=lambda seed: _stable_key(seed, salt="validator-clean"),
    )[:20]
    return sorted(
        {_variant_path(dataset_root, str(record["variant_id"])) for record in selected}
        | {_seed_path(dataset_root, seed) for seed in clean_seeds}
    )


def _paired_model_configs(claude_model: str) -> list[dict[str, Any]]:
    # SweepSpec always expands ``base`` once.  The base is the explicit Codex
    # configuration; this list adds the paired Claude configuration without a
    # duplicate first trial.
    return [
        {
            "provider_override": "claude_cli",
            "model_override": claude_model,
            # Preserve Claude Code's native adaptive/default thinking instead
            # of inheriting the explicit Codex reasoning level from the base.
            "reasoning_effort": None,
        },
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
            "Balanced 100-case STRUCT/SOUND panel with clean controls; "
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


def build_specs(
    *,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    gpt_model: str,
    claude_model: str,
    winner_provider: str,
    winner_model: str,
    winner_pipeline: str = "full",
    repeats: int = DEFAULT_REPEATS,
    semantic_panel_size: int = DEFAULT_SEMANTIC_PANEL_SIZE,
) -> dict[str, dict[str, Any]]:
    """Build plain-Python spec payloads without writing or running them."""

    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    if winner_pipeline not in PIPELINES:
        raise ValueError(f"unknown winner pipeline: {winner_pipeline}")
    records = read_ground_truth(dataset_root)
    all_seeds = {str(record["seed"]) for record in records}

    dev_semantic = _semantic_panel(
        records,
        dataset_root=dataset_root,
        size=semantic_panel_size,
    )
    validator_inputs = _validator_panel(records, dataset_root=dataset_root)
    ablation_inputs = _small_semantic_sample(
        records, dataset_root=dataset_root, seeds=all_seeds
    )
    confirmatory_inputs = _confirmatory_panel(records, dataset_root=dataset_root)
    confirmatory_tiers, confirmatory_scope = PIPELINES[winner_pipeline]

    return {
        "model-selection": _semantic_spec(
            experiment_id="model-selection",
            inputs=dev_semantic,
            description_root=_description_root(dataset_root),
            base=_model_base(
                "codex_cli",
                gpt_model,
                scope="semantic",
                reasoning_effort="medium",
                include_semantic_projection=True,
            ),
            configs=_paired_model_configs(claude_model),
            repeats=repeats,
            notes=(
                f"Balanced {semantic_panel_size}-case semantic panel; paired "
                "GPT uses medium reasoning; Claude CLI uses default thinking."
            ),
        ),
        "ir-selection": _semantic_spec(
            experiment_id="ir-selection",
            inputs=dev_semantic,
            description_root=_description_root(dataset_root),
            base=_model_base(winner_provider, winner_model, scope="semantic"),
            axes={"ir_format": list(IR_FORMATS)},
            repeats=repeats,
            notes=f"Balanced {semantic_panel_size}-case semantic panel across all IRs.",
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
            notes="Balanced 100-case single, disjoint, interacting, and clean panel with the frozen winning configuration.",
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
    semantic_panel_size: int = DEFAULT_SEMANTIC_PANEL_SIZE,
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
            semantic_panel_size=semantic_panel_size,
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
    parser.add_argument(
        "--semantic-panel-size",
        type=int,
        choices=sorted(SEMANTIC_PANEL_QUOTAS),
        default=DEFAULT_SEMANTIC_PANEL_SIZE,
    )
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
        semantic_panel_size=args.semantic_panel_size,
    )
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
