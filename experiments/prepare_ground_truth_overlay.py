"""Build a versioned, reviewable semantic ground-truth overlay.

The frozen dataset truth describes injected mutations, but it does not certify
that source seeds are semantically clean. It also sometimes localizes a deletion
to an element that no longer exists in the variant. This tool keeps the frozen
truth untouched and derives a review overlay with:

* an explicit ``unreviewed`` baseline record for every seed; and
* a surviving construction footprint for every injected defect.

No model is contacted. Baseline findings must be filled by human adjudicators.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from app.model.formats.pydantic_ir import PydanticConverter


SCHEMA_NAME = "bpmn-eval-ground-truth-overlay"
SCHEMA_VERSION = "1.2"


def _labels(defect: dict[str, Any]) -> list[str]:
    value = defect.get("expected_findings", defect.get("expected_finding"))
    if isinstance(value, str):
        return [part.strip() for part in value.split("+") if part.strip()]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str) and item]
    return []


def _reference_lines(defect: dict[str, Any]) -> list[int]:
    anchor = defect.get("anchor")
    if not isinstance(anchor, dict):
        return []
    lines = anchor.get("description_lines", [])
    if not isinstance(lines, list):
        return []
    return [
        int(row["line"])
        for row in lines
        if isinstance(row, dict) and isinstance(row.get("line"), int)
    ]


def _variant_element_ids(path: Path) -> set[str]:
    diagram = PydanticConverter().parse(path.read_bytes())
    return set(diagram.element_ids())


def _surviving_repair_boundary(
    repair: object, present_ids: set[str]
) -> list[str]:
    """Recover surviving neighbors when a mutation deletes its entire site."""
    if not isinstance(repair, list):
        return []
    candidates: set[str] = set()
    for operation in repair:
        if not isinstance(operation, dict):
            continue
        for key in ("id", "flow_id", "source_ref", "target_ref"):
            value = operation.get(key)
            if isinstance(value, str) and value in present_ids:
                candidates.add(value)
    return sorted(candidates)


def build_overlay(dataset_root: Path) -> dict[str, Any]:
    """Create an overlay template without assigning semantic baseline labels."""
    records: dict[str, Any] = {}
    for seed_path in sorted((dataset_root / "seeds").glob("*.bpmn")):
        records[f"seeds/{seed_path.stem}"] = {
            "baseline": {
                "status": "unreviewed",
                "human_verified": False,
                "findings": [],
            }
        }

    ground_truth_root = dataset_root / "ground_truth"
    for truth_path in sorted(ground_truth_root.rglob("*.json")):
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        variant_id = truth.get("variant_id")
        seed = truth.get("seed")
        if not isinstance(variant_id, str) or not isinstance(seed, str):
            raise ValueError(f"invalid variant_id/seed in {truth_path}")
        variant_path = dataset_root / "variants" / f"{variant_id}.bpmn"
        present_ids = _variant_element_ids(variant_path)
        defects = truth.get("defects")
        if not isinstance(defects, list) or not defects:
            defects = [truth]

        injected: list[dict[str, Any]] = []
        for defect in defects:
            if not isinstance(defect, dict):
                continue
            injection_site = defect.get("injection_site", [])
            target_ids = defect.get("expected_elements", [])
            surviving_injection_refs = {
                str(ref)
                for ref in injection_site
                if isinstance(ref, str) and ref in present_ids
            }
            surviving_expected_refs = {
                str(ref)
                for ref in target_ids
                if isinstance(ref, str) and ref in present_ids
            }
            # The construction site names what changed; expected elements can
            # also name an unchanged semantic sibling (for example the other
            # task in a distinct-label relation). Both are valid anchors.
            surviving_refs = sorted(
                surviving_injection_refs | surviving_expected_refs
            )
            derived_from = "semantic_anchor_intersection"
            if not surviving_refs:
                surviving_refs = _surviving_repair_boundary(
                    truth.get("repair"), present_ids
                )
                derived_from = "repair_boundary_intersection"
            injected.append(
                {
                    "operator": defect.get("operator"),
                    "categories": _labels(defect),
                    "target_ids_in_seed": [
                        str(ref)
                        for ref in target_ids
                        if isinstance(ref, str) and ref
                    ],
                    "reference_lines": _reference_lines(defect),
                    "localization": {
                        "mode": "construction_footprint",
                        "allowed_variant_refs": surviving_refs,
                        "match": "any_overlap",
                        "derived_from": derived_from,
                    },
                }
            )

        records[variant_id] = {
            "baseline_inheritance": f"seeds/{seed}",
            "injected": injected,
        }

    return {
        "schema": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "base_dataset": dataset_root.name,
        "baseline_policy": (
            "Unreviewed seeds are not clean controls. Promote a seed to clean, "
            "defect, or ambiguous only through human adjudication."
        ),
        "records": records,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    overlay = build_overlay(args.dataset_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(overlay, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
