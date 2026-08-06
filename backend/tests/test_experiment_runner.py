"""Sweep runner: spec expansion, trial identity, execution and resume."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.experiment_runner import (
    MANIFEST_FILENAME,
    RESULTS_FILENAME,
    SweepSpec,
    build_trials,
    completed_trial_ids,
    execute_sweep,
    expand_configs,
    load_spec,
    resolve_inputs,
    run_trial,
    trial_id,
)
from app.experiments import ExperimentConfig, IrFormat, RepairMode

# a quick-fix rule case: repairs deterministically without reaching a provider
QUICK_FIX_INPUT = Path("data/rule_cases/R001_no_start_event.broken.bpmn")
# duplicate xsd:IDs, rejected by the canonical model at parse time
UNPARSEABLE_INPUT = Path("data/test_cases/02_duplicate_ids.bpmn")


def _spec(**overrides) -> SweepSpec:
    payload = {
        "experiment_id": "unit",
        "inputs": [str(QUICK_FIX_INPUT)],
        "base": {"tiers_enabled": {"t1": True, "t2": False, "t3": False}},
    }
    payload.update(overrides)
    return SweepSpec.model_validate(payload)


# ----- spec expansion -------------------------------------------------------


def test_axes_expand_as_a_cartesian_product():
    spec = _spec(
        axes={
            "ir_format": ["yaml", "mermaid"],
            "repair_mode": ["atomic", "regen"],
        }
    )
    configs = expand_configs(spec)

    assert len(configs) == 4
    assert {(str(c.ir_format), str(c.repair_mode)) for c in configs} == {
        ("yaml", "atomic"),
        ("yaml", "regen"),
        ("mermaid", "atomic"),
        ("mermaid", "regen"),
    }


def test_the_base_config_reaches_every_expanded_config():
    spec = _spec(base={"max_repair_iters": 3}, axes={"ir_format": ["yaml", "mermaid"]})
    assert all(config.max_repair_iters == 3 for config in expand_configs(spec))


def test_an_axis_value_overrides_the_base():
    spec = _spec(base={"repair_mode": "atomic"}, axes={"repair_mode": ["regen"]})
    assert expand_configs(spec)[0].repair_mode == RepairMode.REGEN


def test_explicit_configs_are_appended_to_the_product():
    spec = _spec(
        axes={"ir_format": ["yaml"]}, configs=[{"ir_format": "compact_json"}]
    )
    formats = [str(config.ir_format) for config in expand_configs(spec)]
    assert formats == ["yaml", "compact_json"]


def test_a_spec_without_axes_yields_the_base_alone():
    assert len(expand_configs(_spec())) == 1


def test_an_invalid_axis_value_fails_at_expansion(monkeypatch):
    """Better a spec error before the sweep than a provider error during it."""
    spec = _spec(axes={"max_repair_iters": [0]})
    with pytest.raises(ValueError):
        expand_configs(spec)


# ----- inputs ---------------------------------------------------------------


def test_globs_expand_and_stay_sorted():
    spec = _spec(inputs=["data/rule_cases/*.broken.bpmn"])
    resolved = resolve_inputs(spec, Path("."))

    assert len(resolved) > 1
    assert resolved == sorted(resolved)


def test_exclude_removes_a_resolved_input():
    spec = _spec(
        inputs=["data/test_cases/*.bpmn"], exclude=[str(UNPARSEABLE_INPUT)]
    )
    assert UNPARSEABLE_INPUT not in resolve_inputs(spec, Path("."))


def test_a_repeated_input_is_resolved_once():
    spec = _spec(inputs=[str(QUICK_FIX_INPUT), "data/rule_cases/R001_*.broken.bpmn"])
    assert resolve_inputs(spec, Path(".")) == [QUICK_FIX_INPUT]


def test_a_missing_literal_input_is_an_error():
    with pytest.raises(FileNotFoundError):
        resolve_inputs(_spec(inputs=["data/nope.bpmn"]), Path("."))


def test_excluding_everything_is_an_error():
    """A sweep over nothing is a spec mistake, not an empty result."""
    spec = _spec(exclude=[str(QUICK_FIX_INPUT)])
    with pytest.raises(ValueError):
        resolve_inputs(spec, Path("."))


# ----- trial identity -------------------------------------------------------


def test_trial_ids_are_stable_across_calls():
    config = ExperimentConfig()
    first = trial_id("exp", QUICK_FIX_INPUT, config, 0)
    second = trial_id("exp", QUICK_FIX_INPUT, config, 0)
    assert first == second


def test_a_different_config_gets_a_different_trial_id():
    a = trial_id("exp", QUICK_FIX_INPUT, ExperimentConfig(), 0)
    b = trial_id("exp", QUICK_FIX_INPUT, ExperimentConfig(ir_format=IrFormat.YAML), 0)
    assert a != b


def test_each_repeat_gets_its_own_trial_id():
    config = ExperimentConfig()
    assert trial_id("exp", QUICK_FIX_INPUT, config, 0) != trial_id(
        "exp", QUICK_FIX_INPUT, config, 1
    )


def test_trial_count_is_inputs_times_configs_times_repeats():
    spec = _spec(axes={"ir_format": ["yaml", "mermaid"]}, repeats=3)
    inputs = resolve_inputs(spec, Path("."))
    configs = expand_configs(spec)
    assert len(build_trials(spec, inputs, configs)) == len(inputs) * 2 * 3


# ----- one trial ------------------------------------------------------------


async def test_a_trial_records_phases_validation_and_the_final_diagram():
    spec = _spec()
    trial = build_trials(spec, [QUICK_FIX_INPUT], expand_configs(spec))[0]

    record = await run_trial(trial)

    assert record.error is None
    assert record.pre_validation is not None
    assert "R001" in record.pre_validation.issue_ids
    assert record.repair is not None
    assert record.post_validation is not None
    assert record.final_diagram is not None
    assert set(record.phases) == {"validate", "repair", "revalidate"}


async def test_the_run_block_names_the_config_and_the_commit():
    spec = _spec()
    trial = build_trials(spec, [QUICK_FIX_INPUT], expand_configs(spec))[0]

    record = await run_trial(trial)

    assert record.run.config_hash
    assert record.run.app_commit
    assert record.run.config is not None
    assert record.run.request_id == trial.trial_id


async def test_a_quick_fix_trial_records_that_no_model_answered():
    """R001 is repaired by the registry, so the record must not name a model."""
    spec = _spec()
    trial = build_trials(spec, [QUICK_FIX_INPUT], expand_configs(spec))[0]

    record = await run_trial(trial)

    assert record.run.model_used == "none"
    assert record.run.model_configured
    assert record.repair is not None
    assert set(record.repair.applied_op_origins) == {"quick_fix"}
    assert len(record.repair.applied_op_origins) == len(record.repair.applied_ops)


async def test_a_trial_records_what_the_import_left_behind(tmp_path):
    """Unsupported elements dropped at parse time must show up in the record."""
    input_path = tmp_path / "with_lanes.bpmn"
    input_path.write_bytes(_XML_WITH_UNSUPPORTED)
    spec = _spec(inputs=[str(input_path)])
    trial = build_trials(spec, [input_path], expand_configs(spec))[0]

    record = await run_trial(trial)

    tags = {element["tag"] for element in record.unsupported_elements}
    assert {"collaboration", "laneSet"} <= tags
    assert "not imported" in (record.unsupported_warning or "")


_XML_WITH_UNSUPPORTED = b"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  id="defs_1" targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:collaboration id="Collaboration_1">
    <bpmn:participant id="Participant_1" processRef="Process_1" />
  </bpmn:collaboration>
  <bpmn:process id="Process_1" isExecutable="true">
    <bpmn:laneSet id="LaneSet_1">
      <bpmn:lane id="Lane_1" name="Reviewers" />
    </bpmn:laneSet>
    <bpmn:startEvent id="start_1" />
    <bpmn:task id="task_1" name="Do it" />
    <bpmn:endEvent id="end_1" />
    <bpmn:sequenceFlow id="sf_1" sourceRef="start_1" targetRef="task_1" />
    <bpmn:sequenceFlow id="sf_2" sourceRef="task_1" targetRef="end_1" />
  </bpmn:process>
</bpmn:definitions>
"""


async def test_a_failing_trial_is_recorded_rather_than_raised():
    """A sweep that aborts on one bad input has to be restarted by hand."""
    spec = _spec(inputs=[str(UNPARSEABLE_INPUT)])
    trial = build_trials(spec, [UNPARSEABLE_INPUT], expand_configs(spec))[0]

    record = await run_trial(trial)

    assert record.error is not None
    assert "Duplicate element ID" in record.error
    assert record.pre_validation is None


async def test_an_input_that_disappeared_mid_sweep_is_recorded_not_raised(tmp_path):
    """Inputs are resolved once, so one that moves later must not abort the sweep."""
    missing = tmp_path / "gone.bpmn"
    missing.write_bytes(b"<x/>")
    spec = _spec(inputs=[str(missing)])
    trial = build_trials(spec, [missing], expand_configs(spec))[0]
    missing.unlink()

    record = await run_trial(trial)

    assert record.error is not None
    assert "FileNotFoundError" in record.error
    assert record.input_bytes == 0
    assert record.pre_validation is None


async def test_payloads_are_withheld_unless_asked_for():
    spec = _spec(base={"tiers_enabled": {"t1": True}, "repair_mode": "regen"})
    trial = build_trials(spec, [QUICK_FIX_INPUT], expand_configs(spec))[0]

    lean = await run_trial(trial, keep_payloads=False)
    full = await run_trial(trial, keep_payloads=True)

    lean_calls = [call for phase in lean.phases.values() for call in phase.calls]
    full_calls = [call for phase in full.phases.values() for call in phase.calls]
    assert all(call.prompt is None for call in lean_calls)
    # size is kept either way, it is the token-reduction denominator
    assert all(call.prompt_chars > 0 for call in lean_calls)
    assert full_calls and all(call.prompt for call in full_calls)


# ----- the sweep ------------------------------------------------------------


async def test_a_sweep_writes_one_line_per_trial_and_a_manifest(tmp_path):
    spec = _spec(axes={"ir_format": ["yaml", "mermaid"]})

    summary = await execute_sweep(spec, out_dir=tmp_path, mock=True)

    results = (tmp_path / RESULTS_FILENAME).read_text().strip().splitlines()
    assert len(results) == summary.executed == 2
    assert all(json.loads(line)["trial_id"] for line in results)

    manifest = json.loads((tmp_path / MANIFEST_FILENAME).read_text())
    assert manifest["experiment_id"] == "unit"
    assert manifest["trial_count"] == 2
    assert manifest["app_commit"]
    assert manifest["input_hashes"]


async def test_resuming_skips_what_is_already_on_disk(tmp_path):
    spec = _spec(axes={"ir_format": ["yaml", "mermaid"]})

    first = await execute_sweep(spec, out_dir=tmp_path, mock=True)
    second = await execute_sweep(spec, out_dir=tmp_path, mock=True)

    assert first.executed == 2
    assert second.executed == 0
    assert second.skipped == 2
    lines = (tmp_path / RESULTS_FILENAME).read_text().strip().splitlines()
    assert len(lines) == 2


async def test_no_resume_reruns_everything(tmp_path):
    spec = _spec()

    await execute_sweep(spec, out_dir=tmp_path, mock=True)
    again = await execute_sweep(spec, out_dir=tmp_path, mock=True, resume=False)

    assert again.executed == 1
    assert len((tmp_path / RESULTS_FILENAME).read_text().strip().splitlines()) == 2


async def test_limit_caps_the_pending_trials(tmp_path):
    spec = _spec(axes={"ir_format": ["yaml", "mermaid", "compact_json"]})

    summary = await execute_sweep(spec, out_dir=tmp_path, mock=True, limit=1)

    assert summary.planned == 3
    assert summary.executed == 1


async def test_the_summary_totals_tokens_across_trials(tmp_path):
    spec = _spec(base={"tiers_enabled": {"t1": True}, "repair_mode": "regen"})

    summary = await execute_sweep(spec, out_dir=tmp_path, mock=True)

    assert summary.usage.calls > 0
    assert summary.usage.total_tokens > 0
    assert summary.usage.complete is True


async def test_a_failed_trial_is_counted_and_still_written(tmp_path):
    spec = _spec(inputs=[str(UNPARSEABLE_INPUT)])

    summary = await execute_sweep(spec, out_dir=tmp_path, mock=True)

    assert summary.failed == 1
    assert summary.executed == 1
    record = json.loads((tmp_path / RESULTS_FILENAME).read_text().strip())
    assert record["error"]


async def test_a_failed_trial_is_not_retried_on_resume(tmp_path):
    """The failure is a result; re-running it would just spend the same money."""
    spec = _spec(inputs=[str(UNPARSEABLE_INPUT)])

    await execute_sweep(spec, out_dir=tmp_path, mock=True)
    second = await execute_sweep(spec, out_dir=tmp_path, mock=True)

    assert second.skipped == 1


def test_a_truncated_final_line_does_not_hide_the_earlier_trials(tmp_path):
    """What a run killed mid-write leaves behind."""
    results = tmp_path / RESULTS_FILENAME
    results.write_text('{"trial_id": "aaa"}\n{"trial_id": "bbb"}\n{"trial_i')

    assert completed_trial_ids(results) == {"aaa", "bbb"}


def test_completed_ids_are_empty_when_nothing_has_run(tmp_path):
    assert completed_trial_ids(tmp_path / RESULTS_FILENAME) == set()


# ----- spec files -----------------------------------------------------------


def test_the_shipped_specs_load_and_expand():
    """A spec that only fails when the sweep starts wastes the setup."""
    for path in sorted(Path("experiments/specs").glob("*.yaml")):
        spec = load_spec(path)
        assert spec.experiment_id
        assert expand_configs(spec)
        assert resolve_inputs(spec, Path("."))


def test_load_spec_reads_json(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"experiment_id": "j", "inputs": ["a.bpmn"]}))
    assert load_spec(path).experiment_id == "j"
