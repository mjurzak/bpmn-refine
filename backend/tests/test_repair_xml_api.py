from fastapi.testclient import TestClient

from app.main import app
from app.services.repair import _strip_code_fences


client = TestClient(app)

# a process with two elements colliding on id "task_main" — rejected by the parser
_DUPLICATE_ID_XML = """<?xml version='1.0' encoding='UTF-8'?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             id="def_dup" targetNamespace="http://bpmn.io/schema/bpmn">
  <process id="proc_dup" isExecutable="false">
    <startEvent id="start_in"><outgoing>f1</outgoing></startEvent>
    <task id="task_main" name="Process item"><incoming>f1</incoming><outgoing>f2</outgoing></task>
    <endEvent id="end_ok"><incoming>f2</incoming></endEvent>
    <task id="task_main" name="Process item (copy)"/>
    <sequenceFlow id="f1" sourceRef="start_in" targetRef="task_main"/>
    <sequenceFlow id="f2" sourceRef="task_main" targetRef="end_ok"/>
  </process>
</definitions>"""

_FIXED_XML = """<?xml version='1.0' encoding='UTF-8'?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             id="def_dup" targetNamespace="http://bpmn.io/schema/bpmn">
  <process id="proc_dup" isExecutable="false">
    <startEvent id="start_in"><outgoing>f1</outgoing></startEvent>
    <task id="task_main" name="Process item"><incoming>f1</incoming><outgoing>f2</outgoing></task>
    <endEvent id="end_ok"><incoming>f2</incoming></endEvent>
    <task id="task_main_2" name="Process item (copy)"/>
    <sequenceFlow id="f1" sourceRef="start_in" targetRef="task_main"/>
    <sequenceFlow id="f2" sourceRef="task_main" targetRef="end_ok"/>
  </process>
</definitions>"""


def test_strip_code_fences_removes_wrapping_fence():
    assert _strip_code_fences("```xml\n<a/>\n```") == "<a/>"
    assert _strip_code_fences("<a/>") == "<a/>"
    assert _strip_code_fences("```\n<a/>\n```") == "<a/>"


def test_repair_xml_returns_diagram_when_fix_parses(monkeypatch):
    async def fake_repair_raw_xml(xml, instruction=None, config=None):
        return _FIXED_XML

    monkeypatch.setattr("app.api.routes.repair.repair_raw_xml", fake_repair_raw_xml)

    response = client.post("/api/v1/repair/xml", json={"xml": _DUPLICATE_ID_XML})

    assert response.status_code == 200
    body = response.json()
    assert body["parseable"] is True
    assert body["parse_error"] is None
    assert body["diagram"]["processes"][0]["id"] == "proc_dup"
    assert body["updated_xml"].startswith("<?xml")
    assert body["run"]["prompt_versions"]["repair_xml"]["name"] == "repair_xml.txt"
    assert body["run"]["converged"] is True


def test_repair_xml_reports_still_unparseable(monkeypatch):
    async def fake_repair_raw_xml(xml, instruction=None, config=None):
        # model failed to dedupe — result still has a duplicate id
        return _DUPLICATE_ID_XML

    monkeypatch.setattr("app.api.routes.repair.repair_raw_xml", fake_repair_raw_xml)

    response = client.post("/api/v1/repair/xml", json={"xml": _DUPLICATE_ID_XML})

    assert response.status_code == 200
    body = response.json()
    assert body["parseable"] is False
    assert body["diagram"] is None
    assert "task_main" in body["parse_error"]
    assert body["run"]["converged"] is False
