import pytest

from fastapi import HTTPException
from app.api.routes.diagrams import DiagramParseRequest, parse_diagram
from app.model.schema import BpmnDiagram
from app.services.diagrams import export_bpmn_xml


async def test_parse_diagram_xml_returns_current_ir_without_session_snapshot():
    response = await parse_diagram(DiagramParseRequest(xml=_minimal_valid_xml()))

    assert response.diagram.processes[0].id == "proc_1"
    assert response.diagram.processes[0].flow_nodes[2].type == "endEvent"
    assert not hasattr(response, "session_id")


async def test_parse_diagram_xml_rejects_invalid_xml():
    with pytest.raises(HTTPException) as exc_info:
        await parse_diagram(DiagramParseRequest(xml="<not-bpmn"))

    assert exc_info.value.status_code == 422
    assert str(exc_info.value.detail).startswith("Failed to parse BPMN XML:")


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
