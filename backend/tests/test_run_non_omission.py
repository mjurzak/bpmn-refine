import pytest

from app.model.schema import BpmnDiagram
from app.services.diagrams import export_bpmn_xml


@pytest.mark.parametrize(
    ("endpoint", "patch_target"),
    [
        (
            "/api/v1/validate",
            "app.api.routes.validate.run_validation",
        ),
        (
            "/api/v1/chat",
            "app.api.routes.chat.chat_diagram",
        ),
        (
            "/api/v1/repair",
            "app.api.routes.repair.repair_diagram",
        ),
    ],
)
async def test_run_endpoints_never_omit_run_on_error(
    monkeypatch,
    endpoint,
    patch_target,
    api_client,
):
    monkeypatch.setattr(patch_target, _failing_service_call)

    response = await api_client.post(endpoint, json=_payload_for(endpoint))

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "forced failure"
    assert "run" in body
    assert body["run"]["request_id"]
    assert body["run"]["timestamp"]


async def _failing_service_call(*args, **kwargs):
    raise RuntimeError("forced failure")


def _chat_message() -> dict:
    return {"role": "user", "content": "Check this process."}


def _payload_for(endpoint: str) -> dict:
    if endpoint.endswith("/validate"):
        return {"diagram": _minimal_valid_diagram()}
    if endpoint.endswith("/repair"):
        return {
            "xml": export_bpmn_xml(BpmnDiagram.model_validate(_minimal_valid_diagram())),
            "issues": [
                {
                    "rule_id": "R001",
                    "severity": "error",
                    "message": "Process has no start event.",
                }
            ],
            "config": {"repair_mode": "regen"},
        }
    return {"messages": [_chat_message()]}


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
