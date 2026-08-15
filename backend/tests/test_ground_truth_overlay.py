import json
from pathlib import Path

from experiments.prepare_ground_truth_overlay import build_overlay


def _write_bpmn(path: Path, *, include_task: bool) -> None:
    task = '<task id="Task_deleted" name="Deleted" />' if include_task else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="Defs">
  <process id="Process_1">
    <startEvent id="Start" />
    {task}
    <endEvent id="End" />
    <sequenceFlow id="Gap" sourceRef="Start" targetRef="End" />
  </process>
</definitions>
""",
        encoding="utf-8",
    )


def test_overlay_marks_seeds_unreviewed_and_uses_surviving_footprint(tmp_path):
    root = tmp_path / "v1.0"
    _write_bpmn(root / "seeds/01.bpmn", include_task=True)
    _write_bpmn(root / "variants/single/M01/01.bpmn", include_task=False)
    truth_path = root / "ground_truth/single/M01/01.json"
    truth_path.parent.mkdir(parents=True)
    truth_path.write_text(
        json.dumps(
            {
                "variant_id": "single/M01/01",
                "seed": "01",
                "defects": [
                    {
                        "operator": "M01",
                        "expected_finding": "missing_step",
                        "expected_elements": ["Task_deleted"],
                        "injection_site": ["Task_deleted", "Start", "Gap", "End"],
                        "anchor": {"description_lines": [{"line": 2, "text": "Do it."}]},
                    }
                ],
                "repair": [],
            }
        ),
        encoding="utf-8",
    )

    overlay = build_overlay(root)

    assert overlay["records"]["seeds/01"]["baseline"] == {
        "status": "unreviewed",
        "human_verified": False,
        "findings": [],
    }
    defect = overlay["records"]["single/M01/01"]["injected"][0]
    assert defect["target_ids_in_seed"] == ["Task_deleted"]
    assert defect["reference_lines"] == [2]
    assert defect["localization"]["allowed_variant_refs"] == ["End", "Gap", "Start"]


def test_overlay_uses_repair_boundary_when_the_whole_site_was_deleted(tmp_path):
    root = tmp_path / "v1.0"
    _write_bpmn(root / "seeds/01.bpmn", include_task=True)
    _write_bpmn(root / "variants/single/M04/01.bpmn", include_task=False)
    truth_path = root / "ground_truth/single/M04/01.json"
    truth_path.parent.mkdir(parents=True)
    truth_path.write_text(
        json.dumps(
            {
                "variant_id": "single/M04/01",
                "seed": "01",
                "defects": [
                    {
                        "operator": "M04",
                        "expected_finding": "missing_exception_handling",
                        "expected_elements": ["Task_deleted"],
                        "injection_site": ["Task_deleted"],
                    }
                ],
                "repair": [
                    {
                        "op": "add_flow",
                        "id": "Restored",
                        "source_ref": "Start",
                        "target_ref": "Task_deleted",
                    },
                    {
                        "op": "add_flow",
                        "id": "Restored_2",
                        "source_ref": "Task_deleted",
                        "target_ref": "End",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    defect = build_overlay(root)["records"]["single/M04/01"]["injected"][0]

    assert defect["localization"]["allowed_variant_refs"] == ["End", "Start"]
    assert (
        defect["localization"]["derived_from"]
        == "repair_boundary_intersection"
    )
