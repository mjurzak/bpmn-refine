"""Focused, provider-free tests for ``experiments.prepare_experiments``."""

from __future__ import annotations

import json
from pathlib import Path

from app.experiment_runner import (
    SweepSpec,
    expand_configs,
    load_spec,
    resolve_descriptions,
    resolve_inputs,
)

from experiments.prepare_experiments import (
    build_specs,
    prepare_experiments,
)


def _dataset(tmp_path: Path) -> Path:
    root = tmp_path / "eval" / "v1.0"
    records = [
        ("single/M01/01", "01", "SEM"),
        ("single/S01/01", "01", "STRUCT"),
        ("single/M02/02", "02", "SEM"),
        ("single/F01/02", "02", "SOUND"),
        ("single/M03/03", "03", "SEM"),
        ("single/S02/03", "03", "STRUCT"),
        ("single/M04/04", "04", "SEM"),
        ("single/F02/04", "04", "SOUND"),
        ("disjoint/M01+M02/01", "01", "SEM"),
    ]
    for variant_id, seed, class_name in records:
        gt = root / "ground_truth" / f"{variant_id}.json"
        gt.parent.mkdir(parents=True, exist_ok=True)
        gt.write_text(
            json.dumps(
                {
                    "variant_id": variant_id,
                    "seed": seed,
                    "class": class_name,
                    "operators": variant_id.split("/")[1].split("+"),
                    "multiplicity": 1 if variant_id.startswith("single/") else 2,
                    "interaction": (
                        "disjoint" if variant_id.startswith("disjoint/") else None
                    ),
                }
            ),
            encoding="utf-8",
        )
        variant = root / "variants" / f"{variant_id}.bpmn"
        variant.parent.mkdir(parents=True, exist_ok=True)
        variant.write_text("<definitions />", encoding="utf-8")

    for seed in {"01", "02", "03", "04"}:
        clean = root / "seeds" / f"{seed}.bpmn"
        clean.parent.mkdir(parents=True, exist_ok=True)
        clean.write_text("<definitions />", encoding="utf-8")
        seed_description = root / "descriptions" / "seeds" / f"{seed}.txt"
        seed_description.parent.mkdir(parents=True, exist_ok=True)
        seed_description.write_text(f"description for seed {seed}", encoding="utf-8")
    for variant_id, _, _ in records:
        description = root / "descriptions" / "variants" / f"{variant_id}.txt"
        description.parent.mkdir(parents=True, exist_ok=True)
        description.write_text(f"description for {variant_id}", encoding="utf-8")
    return root


def _args(root: Path, out: Path) -> dict[str, object]:
    return {
        "dataset_root": root,
        "output_dir": out,
        "gpt_model": "gpt-test",
        "claude_model": "claude-test",
        "winner_provider": "codex_cli",
        "winner_model": "gpt-test",
    }


def test_generated_specs_are_native_runner_specs_and_have_expected_controls(tmp_path):
    root = _dataset(tmp_path)
    specs = build_specs(
        **{
            key: value
            for key, value in _args(root, tmp_path / "out").items()
            if key != "output_dir"
        }
    )

    model = specs["model-selection"]
    assert model["inputs"]
    assert model["run_repair"] is False
    assert model["description_root"] == str(root / "descriptions")
    assert all("/disjoint/" not in path for path in model["inputs"])
    assert any("/seeds/" in path for path in model["inputs"])
    assert model["base"]["ir_format"] == "pydantic"
    assert [
        (config.provider_override, config.model_override)
        for config in expand_configs(SweepSpec.model_validate(model))
    ] == [
        ("codex_cli", "gpt-test"),
        ("claude_cli", "claude-test"),
    ]

    ir = specs["ir-selection"]
    assert ir["run_repair"] is False
    assert ir["axes"]["ir_format"] == [
        "pydantic",
        "pydantic_json",
        "yaml",
        "mermaid",
        "compact_json",
    ]
    assert ir["base"]["llm_validation_scope"] == "semantic"
    assert all("/disjoint/" not in path for path in ir["inputs"])

    validator = specs["validator-contribution"]
    assert validator["run_repair"] is False
    assert "description_root" not in validator
    assert len(validator["configs"]) == 2
    assert [validator["base"]["tiers_enabled"]] + [
        config["tiers_enabled"] for config in validator["configs"]
    ] == [
        {"t1": True, "t2": True, "t3": False},
        {"t1": False, "t2": False, "t3": True},
        {"t1": True, "t2": True, "t3": True},
    ]
    assert [validator["base"]["llm_validation_scope"]] + [
        config["llm_validation_scope"] for config in validator["configs"]
    ] == [
        "semantic",
        "holistic",
        "holistic",
    ]
    assert any("/seeds/" in path for path in validator["inputs"])

    assert specs["reasoning-ablation"]["axes"]["reasoning_effort"] == [
        "low",
        "medium",
        "high",
    ]
    description_configs = expand_configs(
        SweepSpec.model_validate(specs["description-ablation"])
    )
    assert [config.include_reference_description for config in description_configs] == [
        False,
        True,
    ]
    confirmatory = specs["confirmatory-detection"]
    assert any("/disjoint/" in path for path in confirmatory["inputs"])
    assert confirmatory["base"]["llm_validation_scope"] == "holistic"

    deterministic = build_specs(
        **{
            key: value
            for key, value in _args(root, tmp_path / "out").items()
            if key != "output_dir"
        },
        winner_pipeline="deterministic",
    )["confirmatory-detection"]
    assert deterministic["base"]["tiers_enabled"] == {
        "t1": True,
        "t2": True,
        "t3": False,
    }


def test_written_specs_load_resolve_and_expand_through_existing_runner(tmp_path, monkeypatch):
    root = _dataset(tmp_path)
    output = tmp_path / "specs"
    args = _args(root, output)
    prepare_experiments(**args)
    monkeypatch.chdir(tmp_path)

    for path in sorted(output.glob("*.yaml")):
        spec = load_spec(path)
        assert spec.experiment_id
        assert expand_configs(spec)
        inputs = resolve_inputs(spec, Path("."))
        assert inputs
        if spec.description_root:
            descriptions = resolve_descriptions(spec, inputs, Path("."))
            assert len(descriptions) == len(inputs)


def test_generation_is_byte_stable_and_repeats_are_overrideable(tmp_path):
    root = _dataset(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    args = _args(root, first)
    args["repeats"] = 3
    prepare_experiments(**args)
    args["output_dir"] = second
    prepare_experiments(**args)

    for name in (
        "model-selection",
        "ir-selection",
        "reasoning-ablation",
        "description-ablation",
        "validator-contribution",
        "confirmatory-detection",
    ):
        assert (first / f"{name}.yaml").read_bytes() == (
            second / f"{name}.yaml"
        ).read_bytes()
        assert load_spec(first / f"{name}.yaml").repeats == 3


def test_checked_in_benchmark_specs_resolve_against_v1_dataset():
    root = Path(".")
    specs = Path("experiments/specs/benchmark")
    expected = {
        "model-selection.yaml",
        "ir-selection.yaml",
        "reasoning-ablation.yaml",
        "description-ablation.yaml",
        "validator-contribution.yaml",
        "confirmatory-detection.yaml",
    }
    assert {path.name for path in specs.glob("*.yaml")} == expected
    for path in specs.glob("*.yaml"):
        spec = load_spec(path)
        assert spec.dataset_version == "v1.0"
        assert resolve_inputs(spec, root)
        assert expand_configs(spec)
    confirmatory = load_spec(specs / "confirmatory-detection.yaml")
    assert len(resolve_inputs(confirmatory, root)) == 591
