from fastapi.testclient import TestClient

from app.experiments import CONVERTER_VERSION, ExperimentConfig, config_hash
from app.main import app
from app.services.chat import ChatResult


def test_chat_response_includes_run_block(monkeypatch):
    captured = {}

    async def fake_chat_diagram(
        messages,
        diagram=None,
        issues=None,
        session_id=None,
        config=None,
    ):
        captured["config"] = config
        return ChatResult(reply="No diagram changes needed.")

    monkeypatch.setattr("app.api.routes.chat.chat_diagram", fake_chat_diagram)

    config = {
        "model_tier": "custom",
        "model_override": "custom-chat-model",
        "experiment_id": "chat-run-test",
    }
    client = TestClient(app)

    response = client.post(
        "/api/v1/chat",
        json={
            "messages": [{"role": "user", "content": "Check this process."}],
            "config": config,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    run = payload["run"]

    assert payload["reply"] == "No diagram changes needed."
    assert captured["config"] == ExperimentConfig(**config)
    assert run["model_used"] == "custom-chat-model"
    assert run["prompt_versions"]["chat"]["name"] == "chat_system.txt"
    assert len(run["prompt_versions"]["chat"]["hash"]) == 12
    assert run["converter"] == CONVERTER_VERSION
    assert run["rules_version"] == "R001-R011"
    assert run["config_hash"] == config_hash(ExperimentConfig(**config))
    assert run["request_id"]
    assert run["timestamp"]
