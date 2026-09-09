from __future__ import annotations

import json
from pathlib import Path

from experiments.run_e7 import _config, analyze, run_selection


def test_frozen_e7_selection_covers_each_available_group_twice_when_possible():
    selection = json.loads(
        Path("experiments/specs/runs/e7-repair-selection.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(selection["cases"]) == 13
    assert selection["unavailable_groups"] == ["interacting_structural"]
    assert set(selection["selected_counts"].values()) == {0, 1, 2}
    config = _config()
    assert config.model_override == "gpt-5.6-terra"
    assert str(config.reasoning_effort) == "low"
    assert str(config.ir_format) == "compact_json"
    assert config.max_repair_iters == 2


async def test_e7_mock_run_checkpoints_and_analyzes_one_case(tmp_path: Path):
    source = json.loads(
        Path("experiments/specs/runs/e7-repair-selection.json").read_text(
            encoding="utf-8"
        )
    )
    source["cases"] = source["cases"][:1]
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps(source), encoding="utf-8")
    out = tmp_path / "run"

    manifest = await run_selection(
        selection, out, _config(), concurrency=1, mock=True, resume=False
    )
    assert manifest["planned"] == manifest["completed"] == 1
    assert manifest["failed"] == 0
    assert len(list((out / "trials").glob("*.json"))) == 1

    report = analyze(out / "results.jsonl", selection)
    assert report["overall"]["cases"] == 1
    assert report["overall"]["targets"] == 2
    assert report["overall"]["errors"] == 0
