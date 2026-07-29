from datetime import UTC, datetime
import hashlib

import pytest
from pydantic import ValidationError

from app.experiments import (
    ExperimentConfig,
    ModelTier,
    PromptVersion,
    ProviderName,
    RunBlock,
    build_run_block,
    canonical_config_json,
    config_hash,
    hash_bytes,
    prompt_version,
)


def test_experiment_config_defaults_match_current_runtime():
    config = ExperimentConfig()

    data = config.model_dump(mode="json")

    assert data["model_tier"] == "strong"
    assert data["ir_format"] == "pydantic"
    assert data["tiers_enabled"] == {"t1": True, "t2": False, "t3": False}
    assert data["t2_tools"] == ["bpmn_analyzer", "woflan", "bpmnspector"]
    assert data["repair_mode"] == "atomic"
    assert data["max_repair_iters"] == 5
    # unset by default: the provider's own default applies and the record says so
    assert data["temperature"] is None
    assert data["seed"] is None
    assert data["include_formal_evidence"] is True


def test_custom_model_tier_requires_model_override():
    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate({"model_tier": "custom"})

    config = ExperimentConfig(
        model_tier=ModelTier.CUSTOM,
        model_override="local-model",
    )

    assert config.model_override == "local-model"


def test_run_block_accepts_required_reproducibility_fields():
    run = RunBlock(
        model_used="gpt-5.4",
        prompt_versions={
            "validate": PromptVersion(name="validate.txt", hash="012345abcdef")
        },
        converter="pydantic_ir@v1",
        rules_version="R001-R008",
        config_hash="abcdef012345",
        timestamp=datetime(2026, 5, 13, tzinfo=UTC),
        request_id="request-1",
    )

    assert run.prompt_versions["validate"].hash == "012345abcdef"


def test_canonical_config_json_drops_nulls_and_sorts_keys():
    config = ExperimentConfig(
        provider_override=ProviderName.OPENAI,
        seed=42,
        experiment_id="exp-1",
    )

    canonical = canonical_config_json(config)

    assert canonical == (
        '{"experiment_id":"exp-1","include_formal_evidence":true,'
        '"ir_format":"pydantic","max_repair_iters":5,'
        '"model_tier":"strong","provider_override":"openai",'
        '"repair_mode":"atomic","seed":42,'
        '"t2_tools":["bpmn_analyzer","woflan","bpmnspector"],'
        '"tiers_enabled":{"t1":true,"t2":false,"t3":false}}'
    )
    # an unset temperature is dropped like any other null: the run record states
    # that the provider default applied, not a value the call never sent
    assert "temperature" not in canonical
    assert "model_override" not in canonical
    assert "notes" not in canonical
    assert ": " not in canonical
    assert ", " not in canonical


def test_config_hash_is_12_char_sha256_prefix():
    config = ExperimentConfig(
        provider_override=ProviderName.OPENAI,
        seed=42,
        experiment_id="exp-1",
    )
    canonical = canonical_config_json(config)

    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    assert config_hash(config) == expected
    assert len(config_hash(config)) == 12


def test_hash_bytes_uses_12_char_sha256_prefix():
    data = b"prompt contents\n"

    expected = hashlib.sha256(data).hexdigest()[:12]

    assert hash_bytes(data) == expected


def test_prompt_version_hashes_file_bytes(tmp_path):
    prompt_path = tmp_path / "validate.txt"
    prompt_path.write_bytes(b"validate prompt\n")

    version = prompt_version(prompt_path)

    assert version.name == "validate.txt"
    assert version.hash == hashlib.sha256(b"validate prompt\n").hexdigest()[:12]


def test_build_run_block_populates_reproducibility_fields(tmp_path):
    prompt_path = tmp_path / "chat_system.txt"
    prompt_path.write_bytes(b"chat prompt\n")
    config = ExperimentConfig(model_tier=ModelTier.FAST, seed=7)
    timestamp = datetime(2026, 5, 13, tzinfo=UTC)

    run = build_run_block(
        config=config,
        model_used="gpt-5-nano",
        converter="pydantic_ir@v1",
        rules_version="R001-R008",
        prompt_files={"chat": prompt_path},
        checkers={"woflan": "pm4py-2.7"},
        request_id="request-123",
        timestamp=timestamp,
        iterations=2,
        converged=False,
    )

    assert run.model_used == "gpt-5-nano"
    assert run.prompt_versions["chat"].name == "chat_system.txt"
    assert run.prompt_versions["chat"].hash == hashlib.sha256(b"chat prompt\n").hexdigest()[:12]
    assert run.converter == "pydantic_ir@v1"
    assert run.rules_version == "R001-R008"
    assert run.checkers == {"woflan": "pm4py-2.7"}
    assert run.config_hash == config_hash(config)
    assert run.timestamp == timestamp
    assert run.request_id == "request-123"
    assert run.iterations == 2
    assert run.converged is False
