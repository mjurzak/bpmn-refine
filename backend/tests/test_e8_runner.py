from __future__ import annotations

import json
from pathlib import Path

from app.e8_runner import _diagram, _requirement, analyze_e8, load_cases, run_e8


async def test_mock_e8_rehearsal_uses_chat_path_and_checkpoints(tmp_path: Path):
    output = tmp_path / "results.jsonl"
    summary = await run_e8(
        Path("data/eval/enhancement/cases.json"), output, mock=True, limit=1
    )

    assert summary["executed"] == 1
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["diagram_returned"] is True
    assert record["config"]
    assert record["config_hash"]
    assert record["tier1_valid"] is True
    assert record["requirement_satisfied"] is True
    assert record["preservation_rate"] == 1.0
    assert record["unnecessary_changes"] == 0
    assert record["ged_to_reference"] == 0
    assert record["manual_review"] == "not_required"
    assert record["full_enhancement_success"] is True

    resumed = await run_e8(
        Path("data/eval/enhancement/cases.json"), output, mock=True, limit=1
    )
    # Checkpointing skips the completed case and advances to the next pending
    # case when a limit is supplied.
    assert resumed["executed"] == 1
    assert resumed["skipped"] == len(json.loads(Path("data/eval/enhancement/cases.json").read_text())["cases"]) - 1
    assert analyze_e8(output)["records"] == 2


async def test_mock_e8_scores_all_included_relation_families(tmp_path: Path):
    output = tmp_path / "all-results.jsonl"
    output.write_text('{"case_id":"stale"}\n', encoding="utf-8")
    await run_e8(Path("data/eval/enhancement/cases.json"), output, mock=True, resume=False)
    records = [json.loads(line) for line in output.read_text().splitlines() if line]
    assert len(records) == 21
    assert {item["requirement_status"] for item in records} >= {
        "exists_task",
        "precedes",
        "branch_condition",
        "exists_path",
        "reaches",
        "policy_removal",
    }
    assert all(item["requirement_satisfied"] is True for item in records)
    assert all(item["preservation_rate"] == 1.0 for item in records)
    assert all(item["unnecessary_changes"] == 0 for item in records)
    assert all(item["ged_to_reference"] == 0 for item in records)
    assert all(item["usage"]["calls"] == 0 for item in records)
    assert all(item["manual_review"] == "not_required" for item in records)
    assert all(item["full_enhancement_success"] is True for item in records)


def test_relation_contract_rejects_each_core_and_accepts_each_reference():
    root = Path("data/eval/enhancement")
    for case in load_cases(root / "cases.json"):
        core = _diagram(root / case["core"])
        reference = _diagram(root / case["reference"])
        assert _requirement(case, core, reference)[0] is False, case["case_id"]
        assert _requirement(case, reference, reference)[0] is True, case["case_id"]


def test_e8_analysis_keeps_tier2_timeout_separate(tmp_path: Path):
    output = tmp_path / "results.jsonl"
    output.write_text(
        json.dumps(
            {
                "case_id": "x",
                "diagram_returned": True,
                "tier1_valid": True,
                "tier2_valid": None,
                "tier2_status": "timeout",
                "requirement_satisfied": True,
                "full_enhancement_success": False,
                "manual_review": "required",
                "preservation_rate": 1.0,
                "unnecessary_changes": 0,
                "ged_to_reference": 0,
                "latency_ms": 4,
                "usage": {"calls": 1, "calls_missing_usage": 1},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = analyze_e8(output)
    assert result["tier2_timeouts"] == 1
    assert result["tier2_valid_rate"] is None
