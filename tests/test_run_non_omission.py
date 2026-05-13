import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.chat import ChatResult


client = TestClient(app)


@pytest.mark.parametrize(
    "endpoint",
    ["/api/v1/validate", "/api/v1/chat"],
)
def test_run_endpoints_never_omit_run_on_success(monkeypatch, endpoint):
    if endpoint.endswith("/chat"):
        monkeypatch.setattr("app.api.routes.chat.chat_diagram", _fake_chat_diagram)

    response = client.post(endpoint, json=_payload_for(endpoint))

    assert response.status_code == 200
    assert "run" in response.json()


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
    ],
)
def test_run_endpoints_never_omit_run_on_error(
    monkeypatch,
    endpoint,
    patch_target,
):
    monkeypatch.setattr(patch_target, _failing_service_call)

    response = client.post(endpoint, json=_payload_for(endpoint))

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "forced failure"
    assert "run" in body
    assert body["run"]["request_id"]
    assert body["run"]["timestamp"]


async def _fake_chat_diagram(*args, **kwargs):
    return ChatResult(reply="No diagram changes needed.")


async def _failing_service_call(*args, **kwargs):
    raise RuntimeError("forced failure")


def _chat_message() -> dict:
    return {"role": "user", "content": "Check this process."}


def _payload_for(endpoint: str) -> dict:
    if endpoint.endswith("/validate"):
        return {"diagram": _minimal_valid_diagram()}
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
