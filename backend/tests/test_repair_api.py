from fastapi.testclient import TestClient

from app.experiments import CONVERTER_VERSION, ExperimentConfig, config_hash
from app.main import app
from app.model.schema import BpmnDiagram
from app.services.diagrams import export_bpmn_xml
from app.services.repair import RepairResult


client = TestClient(app)


def test_repair_response_includes_run_and_proposed_xml(monkeypatch):
    captured = {}

    async def fake_repair_diagram(
        diagram,
        issues,
        session_id=None,
        config=None,
        snapshot=True,
    ):
        captured["config"] = config
        captured["snapshot"] = snapshot
        return RepairResult(repaired_diagram=diagram)

    monkeypatch.setattr("app.api.routes.repair.repair_diagram", fake_repair_diagram)

    config = {
        "model_tier": "custom",
        "model_override": "custom-repair-model",
        "repair_mode": "regen",
        "experiment_id": "repair-run-test",
    }

    response = client.post(
        "/api/v1/repair",
        json={
            "xml": _minimal_valid_xml(),
            "issues": [
                {
                    "rule_id": "R001",
                    "severity": "error",
                    "message": "Process has no start event.",
                }
            ],
            "config": config,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    run = payload["run"]

    assert payload["updated_xml"].startswith("<?xml")
    assert payload["input_diagram"]["processes"][0]["id"] == "proc_1"
    assert [op["op"] for op in payload["applied_ops"]] == ["replace_diagram"]
    assert payload["remaining_issues"] == []
    assert payload["iterations"] == 1
    assert payload["converged"] is True
    expected_config = ExperimentConfig.model_validate(config)
    assert captured["config"] == expected_config
    assert captured["snapshot"] is False
    assert run["model_used"] == "custom-repair-model"
    assert run["prompt_versions"]["repair"]["name"] == "repair.txt"
    assert len(run["prompt_versions"]["repair"]["hash"]) == 12
    assert run["converter"] == CONVERTER_VERSION
    assert run["rules_version"] == "R001-R008"
    assert run["config_hash"] == config_hash(expected_config)
    assert run["iterations"] == 1
    assert run["converged"] is True


def test_repair_rejects_empty_issue_list_with_run():
    response = client.post(
        "/api/v1/repair",
        json={"xml": _minimal_valid_xml(), "issues": []},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "Repair requires at least one validation issue."
    assert "run" in body
    assert body["run"]["iterations"] == 0
    assert body["run"]["converged"] is False


def test_apply_selected_edit_ops_returns_updated_diagram():
    response = client.post(
        "/api/v1/repair/apply",
        json={
            "diagram": _minimal_valid_diagram(),
            "ops": [
                {
                    "op": "rename_node",
                    "id": "task_1",
                    "new_name": "Reviewed task",
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    process = body["updated_diagram"]["processes"][0]
    task = next(node for node in process["flow_nodes"] if node["id"] == "task_1")

    assert task["name"] == "Reviewed task"
    assert body["op_results"][0]["applied"] is True
    assert body["updated_xml"].startswith("<?xml")


def _minimal_valid_xml() -> str:
    return export_bpmn_xml(BpmnDiagram.model_validate(_minimal_valid_diagram()))


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
