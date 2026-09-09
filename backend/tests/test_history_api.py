from app.history import service as history_service


async def test_revision_history_workflow(monkeypatch, tmp_path, api_client):
    monkeypatch.setattr(history_service, "_workspace_root", lambda: tmp_path)

    committed = await api_client.post(
        "/api/v1/history/commit",
        json={
            "diagram": _minimal_valid_diagram(),
            "message": "accepted repair",
            "author": "user",
        },
    )
    assert committed.status_code == 200
    session_id = committed.json()["session_id"]
    assert committed.json()["new_rev_id"] == "0000"

    history = await api_client.get(f"/api/v1/history/{session_id}")
    assert history.status_code == 200
    assert [revision["rev_id"] for revision in history.json()["revisions"]] == [
        "0000"
    ]

    revision = await api_client.get(f"/api/v1/history/{session_id}/0000")
    assert revision.status_code == 200
    assert revision.json()["message"] == "accepted repair"

    reverted = await api_client.post(f"/api/v1/history/{session_id}/revert/0000")
    assert reverted.status_code == 200
    assert reverted.json()["new_rev_id"] == "0001"
    assert reverted.json()["diagram"] == committed.json()["diagram"]


def _minimal_valid_diagram() -> dict:
    return {
        "definitions_id": "def_1",
        "processes": [
            {
                "id": "proc_1",
                "flow_nodes": [
                    {"id": "start_1", "type": "startEvent", "outgoing": ["sf_1"]},
                    {
                        "id": "end_1",
                        "type": "endEvent",
                        "incoming": ["sf_1"],
                    },
                ],
                "sequence_flows": [
                    {
                        "id": "sf_1",
                        "source_ref": "start_1",
                        "target_ref": "end_1",
                    }
                ],
            }
        ],
    }
