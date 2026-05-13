from fastapi.testclient import TestClient

from app.experiments import ExperimentConfig, config_hash
from app.main import app


client = TestClient(app)


def test_validate_response_includes_run_block():
    config = {
        "model_tier": "fast",
        "seed": 7,
        "experiment_id": "validate-run-test",
    }

    response = client.post(
        "/api/v1/validate",
        json={
            "diagram": _minimal_valid_diagram(),
            "include_semantic": False,
            "config": config,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    run = payload["run"]

    assert payload["is_valid"] is True
    assert run["model_used"] == "none"
    assert run["prompt_versions"] == {}
    assert run["converter"] == "pydantic_ir@v1"
    assert run["rules_version"] == "R001-R011"
    assert run["config_hash"] == config_hash(ExperimentConfig(**config))
    assert len(run["config_hash"]) == 12
    assert run["request_id"]
    assert run["timestamp"]


def _minimal_valid_diagram() -> dict:
    return {
        "definitions_id": "def_1",
        "processes": [
            {
                "id": "proc_1",
                "flow_nodes": [
                    {"id": "start_1", "type": "startEvent", "outgoing": ["sf_1"]},
                    {
                        "id": "task_1",
                        "type": "task",
                        "name": "Do something",
                        "incoming": ["sf_1"],
                        "outgoing": ["sf_2"],
                    },
                    {"id": "end_1", "type": "endEvent", "incoming": ["sf_2"]},
                ],
                "sequence_flows": [
                    {"id": "sf_1", "source_ref": "start_1", "target_ref": "task_1"},
                    {"id": "sf_2", "source_ref": "task_1", "target_ref": "end_1"},
                ],
            }
        ],
    }
