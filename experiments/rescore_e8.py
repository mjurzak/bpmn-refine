"""Rescore stored E8 diagrams without making provider calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.e8_runner import (
    _diagram,
    _manual_status,
    _path,
    _preservation,
    _requirement,
    _structural_distance,
    load_cases,
)
from app.model.schema import BpmnDiagram


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", type=Path)
    parser.add_argument("results", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()

    root = args.root or args.cases.parent
    cases = {str(case["case_id"]): case for case in load_cases(args.cases)}
    rows = [
        json.loads(line)
        for line in args.results.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rescored: list[dict[str, object]] = []
    for original in rows:
        row = dict(original)
        case = cases[str(row["case_id"])]
        core = _diagram(_path(root, str(case["core"])))
        reference = _diagram(_path(root, str(case["reference"])))
        payload = row.get("final_diagram")
        result = BpmnDiagram.model_validate(payload) if payload is not None else None
        value, status = _requirement(case, result, reference, core)
        preservation, unnecessary = (
            _preservation(case, core, reference, result)
            if result is not None
            else (None, 0)
        )
        row["original_requirement_satisfied"] = row.get("requirement_satisfied")
        row["original_requirement_status"] = row.get("requirement_status")
        row["requirement_satisfied"] = value
        row["requirement_status"] = status
        row["manual_review"] = _manual_status(status)
        row["preservation_rate"] = preservation
        row["unnecessary_changes"] = unnecessary
        row["ged_to_reference"] = (
            _structural_distance(result, reference) if result is not None else None
        )
        row["full_enhancement_success"] = bool(
            result is not None
            and row.get("tier1_valid") is True
            and row.get("tier2_valid") is not False
            and value is True
            and row["manual_review"] == "not_required"
        )
        row["scorer_version"] = "e8-relation-label-graph@v2"
        rescored.append(row)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rescored
        ),
        encoding="utf-8",
    )
    print(json.dumps({"records": len(rescored), "out": str(args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
