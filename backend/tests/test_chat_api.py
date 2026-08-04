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
        snapshot_changes=True,
    ):
        captured["config"] = config
        captured["snapshot_changes"] = snapshot_changes
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
    expected_config = ExperimentConfig.model_validate(config)
    assert captured["config"] == expected_config
    assert captured["snapshot_changes"] is True
    assert run["model_used"] == "custom-chat-model"
    assert run["prompt_versions"]["chat"]["name"] == "chat_system.txt"
    assert len(run["prompt_versions"]["chat"]["hash"]) == 12
    assert run["converter"] == CONVERTER_VERSION
    assert run["rules_version"] == "R001-R008"
    assert run["config_hash"] == config_hash(expected_config)
    assert run["request_id"]
    assert run["timestamp"]


def test_chat_can_return_unsnapshotted_proposal(monkeypatch):
    captured = {}

    async def fake_chat_diagram(
        messages,
        diagram=None,
        issues=None,
        session_id=None,
        config=None,
        snapshot_changes=True,
    ):
        captured["snapshot_changes"] = snapshot_changes
        return ChatResult(reply="Proposal only.")

    monkeypatch.setattr("app.api.routes.chat.chat_diagram", fake_chat_diagram)

    client = TestClient(app)
    response = client.post(
        "/api/v1/chat",
        json={
            "messages": [{"role": "user", "content": "Change this process."}],
            "snapshot_changes": False,
        },
    )

    assert response.status_code == 200
    assert captured["snapshot_changes"] is False


async def test_a_rejected_envelope_still_returns_the_model_text(monkeypatch):
    """the fallback promises the user the model's prose, not an empty bubble

    Reading `description` only after the envelope validated left a rejected
    envelope — missing `result` here — showing nothing at all.
    """
    from app.services import chat as chat_service

    calls = 0

    async def fake_complete_structured_with_history(**kwargs):
        nonlocal calls
        calls += 1
        # well-formed prose, malformed envelope — `result` is missing entirely
        return {"description": "Here is what I would change and why."}

    monkeypatch.setattr(
        chat_service.llm_client,
        "complete_structured_with_history",
        fake_complete_structured_with_history,
    )

    result = await chat_service.chat_diagram(
        messages=[chat_service.ChatMessage(role="user", content="Refine it.")],
    )

    assert result.reply == "Here is what I would change and why."
    assert result.updated_diagram is None
    assert calls == 2
