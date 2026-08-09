import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.routes import dataset
from app.main import app

client = TestClient(app)


def _write_dataset(root: Path) -> None:
    version = root / "v-test"
    for name in ("seeds", "variants", "ground_truth"):
        (version / name).mkdir(parents=True, exist_ok=True)
    (version / "seeds" / "01.bpmn").write_text("<original />", encoding="utf-8")
    (version / "variants" / "01_S01_Start_1.bpmn").write_text(
        "<variant />", encoding="utf-8"
    )
    (version / "ground_truth" / "01_S01_Start_1.json").write_text(
        json.dumps(
            {
                "variant_id": "01_S01_Start_1",
                "seed": "01",
                "operators": ["S01"],
                "class": "STRUCT",
                "expected_finding": "R001",
                "injection_site": ["Start_1"],
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
                        "id": "01_S01_Start_1",
                        "operators": ["S01"],
                        "defect_class": "STRUCT",
                        "expected_finding": "R001",
                        "injection_site": ["Start_1"],
                    }
                ],
            }
        ],
    }


def test_dataset_comparison_returns_the_matched_pair(tmp_path, monkeypatch):
    _write_dataset(tmp_path)
    monkeypatch.setattr(dataset, "DATASET_ROOT", tmp_path)

    response = client.get(
        "/api/v1/evaluation/datasets/v-test/comparisons/01_S01_Start_1"
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
