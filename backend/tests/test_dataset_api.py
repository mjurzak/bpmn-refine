import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.routes import dataset
from app.main import app

client = TestClient(app)


def _write_dataset(root: Path) -> None:
    version = root / "v-test"
    for name in ("seeds", "variants", "ground_truth", "descriptions/seeds"):
        (version / name).mkdir(parents=True, exist_ok=True)
    (version / "seeds" / "01.bpmn").write_text("<original />", encoding="utf-8")
    (version / "descriptions" / "seeds" / "01.txt").write_text(
        "The worker checks the request.\nThe worker approves it.\n",
        encoding="utf-8",
    )
    relative = Path("single/S01/01")
    variant_path = version / "variants" / relative.with_suffix(".bpmn")
    truth_path = version / "ground_truth" / relative.with_suffix(".json")
    variant_path.parent.mkdir(parents=True)
    truth_path.parent.mkdir(parents=True)
    variant_path.write_text("<variant />", encoding="utf-8")
    truth_path.write_text(
        json.dumps(
            {
                "variant_id": "single/S01/01",
                "seed": "01",
                "operators": ["S01"],
                "class": "STRUCT",
                "expected_finding": "R001",
                "injection_site": ["Start_1"],
            }
        ),
        encoding="utf-8",
    )


def _write_enhancement_dataset(root: Path) -> None:
    enhancement = root / "enhancement"
    case_root = enhancement / "candidates" / "M01" / "01"
    case_root.mkdir(parents=True)
    (case_root / "core.bpmn").write_text("<core />", encoding="utf-8")
    (case_root / "reference.bpmn").write_text("<reference />", encoding="utf-8")
    (enhancement / "cases.json").write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "M01-refine-01",
                        "seed_id": "01",
                        "core": "candidates/M01/01/core.bpmn",
                        "reference": "candidates/M01/01/reference.bpmn",
                        "instruction": "Add the missing approval task.",
                        "d_core": "The request arrives.",
                        "d_extra": "The worker approves the request.",
                        "relation": {"type": "exists_task"},
                        "expected_element_ids": ["Task_1"],
                        "metadata": {"operator": "M01"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_dataset_index_groups_variants_under_their_seed(tmp_path, monkeypatch):
    _write_dataset(tmp_path)
    monkeypatch.setattr(dataset, "DATASET_ROOT", tmp_path)

    response = client.get("/api/v1/evaluation/datasets/v-test")

    assert response.status_code == 200
    assert response.json() == {
        "version": "v-test",
        "seeds": [
            {
                "id": "01",
                "variants": [
                    {
                        "id": "single/S01/01",
                        "operators": ["S01"],
                        "defect_class": "STRUCT",
                        "expected_finding": "R001",
                        "injection_site": ["Start_1"],
                        "multiplicity": 1,
                        "interaction": "single",
                    }
                ],
            }
        ],
    }


def test_dataset_comparison_returns_the_matched_pair(tmp_path, monkeypatch):
    _write_dataset(tmp_path)
    monkeypatch.setattr(dataset, "DATASET_ROOT", tmp_path)

    response = client.get(
        "/api/v1/evaluation/datasets/v-test/comparisons/single/S01/01"
    )

    assert response.status_code == 200
    assert response.json()["seed"] == "01"
    assert response.json()["original_xml"] == "<original />"
    assert response.json()["variant_xml"] == "<variant />"


def test_dataset_api_rejects_unknown_or_unsafe_paths(tmp_path, monkeypatch):
    _write_dataset(tmp_path)
    monkeypatch.setattr(dataset, "DATASET_ROOT", tmp_path)

    missing = client.get("/api/v1/evaluation/datasets/missing")
    unsafe = client.get("/api/v1/evaluation/datasets/v-test/comparisons/not-found")

    assert missing.status_code == 404
    assert unsafe.status_code == 404


def test_enhancement_index_exposes_case_metadata(tmp_path, monkeypatch):
    _write_enhancement_dataset(tmp_path)
    monkeypatch.setattr(dataset, "DATASET_ROOT", tmp_path)

    response = dataset.enhancement_index().model_dump(mode="json")

    assert response["cases"] == [
        {
            "id": "M01-refine-01",
            "seed_id": "01",
            "operator": "M01",
            "instruction": "Add the missing approval task.",
            "relation_type": "exists_task",
            "expected_element_ids": ["Task_1"],
        }
    ]


def test_enhancement_comparison_returns_core_reference_and_contract(
    tmp_path, monkeypatch
):
    _write_enhancement_dataset(tmp_path)
    monkeypatch.setattr(dataset, "DATASET_ROOT", tmp_path)

    response = dataset.enhancement_comparison("M01-refine-01")

    assert response.core_xml == "<core />"
    assert response.reference_xml == "<reference />"
    assert response.relation == {"type": "exists_task"}
    assert response.d_extra == "The worker approves the request."
