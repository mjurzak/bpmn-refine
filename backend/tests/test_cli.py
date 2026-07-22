from pathlib import Path

from typer.testing import CliRunner

from app.cli import app
from app.model.schema import (
    BpmnDiagram,
    BpmnProcess,
    FlowNode,
    FlowNodeType,
    SequenceFlow,
)
from app.services.repair import RepairResult, UnresolvedRepair

runner = CliRunner()


def test_validate_command_reports_valid_diagram():
    result = runner.invoke(app, ["validate", "data/import_cases/pmo_01.bpmn"])

    assert result.exit_code == 0
    assert "VALID" in result.stdout


def test_validate_command_json_output_for_invalid_file(tmp_path: Path):
    invalid_bpmn = tmp_path / "invalid.bpmn"
    invalid_bpmn.write_text(
        """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<definitions xmlns=\"http://www.omg.org/spec/BPMN/20100524/MODEL\" id=\"defs\" targetNamespace=\"http://bpmn.io/schema/bpmn\">
  <process id=\"process_1\" isExecutable=\"false\">
    <task id=\"task_1\" name=\"Standalone task\" />
  </process>
</definitions>
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(invalid_bpmn), "--json"])

    assert result.exit_code == 1
    assert '"is_valid": false' in result.stdout
    assert '"rule_id": "R001"' in result.stdout
    assert '"metadata": {' in result.stdout


def test_roundtrip_command_writes_output(tmp_path: Path):
    out_path = tmp_path / "roundtrip.bpmn"

    result = runner.invoke(
        app,
        ["roundtrip", "data/import_cases/pmo_01.bpmn", "--out", str(out_path)],
    )

    assert result.exit_code == 0
    assert out_path.exists()
    assert "<definitions" in out_path.read_text(encoding="utf-8")


def test_chat_command_writes_updated_diagram(monkeypatch, tmp_path: Path):
    async def fake_chat_diagram(messages, diagram=None, issues=None, session_id=None):
        updated_diagram = BpmnDiagram(
            definitions_id="defs_1",
            processes=[
                BpmnProcess(
                    id="process_1",
                    flow_nodes=[
                        FlowNode(
                            id="start_1",
                            type=FlowNodeType.START_EVENT,
                            outgoing=["sf_1"],
                        ),
                        FlowNode(
                            id="task_1",
                            type=FlowNodeType.TASK,
                            incoming=["sf_1"],
                            outgoing=["sf_2"],
                        ),
                        FlowNode(
                            id="end_1", type=FlowNodeType.END_EVENT, incoming=["sf_2"]
                        ),
                    ],
                    sequence_flows=[
                        SequenceFlow(
                            id="sf_1", source_ref="start_1", target_ref="task_1"
                        ),
                        SequenceFlow(
                            id="sf_2", source_ref="task_1", target_ref="end_1"
                        ),
                    ],
                )
            ],
        )
        from app.services.chat import ChatResult

        return ChatResult(
            reply="Applied your requested change.",
            updated_diagram=updated_diagram,
            rev_id="0001",
            session_id="session-123",
        )

    monkeypatch.setattr("app.cli.chat_diagram", fake_chat_diagram)

    out_path = tmp_path / "refined.bpmn"
    result = runner.invoke(
        app,
        [
            "chat",
            "data/import_cases/pmo_01.bpmn",
            "--message",
            "add approval",
            "--no-issues-from-validate",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0
    assert out_path.exists()
    assert "Applied your requested change." in result.stdout
    assert "<definitions" in out_path.read_text(encoding="utf-8")


def test_batch_validate_command_writes_jsonl_and_fails_on_invalid(tmp_path: Path):
    invalid_bpmn = tmp_path / "invalid.bpmn"
    invalid_bpmn.write_text(
        """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<definitions xmlns=\"http://www.omg.org/spec/BPMN/20100524/MODEL\" id=\"defs\" targetNamespace=\"http://bpmn.io/schema/bpmn\">
  <process id=\"process_1\" isExecutable=\"false\">
    <task id=\"task_1\" name=\"Standalone task\" />
  </process>
</definitions>
""",
        encoding="utf-8",
    )
    out_path = tmp_path / "results.jsonl"

    result = runner.invoke(
        app,
        [
            "batch-validate",
            str(tmp_path),
            "--out",
            str(out_path),
            "--fail-on-error",
        ],
    )

    assert result.exit_code == 1
    assert out_path.exists()
    output = out_path.read_text(encoding="utf-8")
    assert '"is_valid": false' in output
    assert str(invalid_bpmn) in output


def test_repair_command_json_output(monkeypatch, tmp_path: Path):
    async def fake_repair_diagram(diagram, issues, session_id=None):
        repaired_diagram = BpmnDiagram(
            definitions_id="defs_1",
            processes=[
                BpmnProcess(
                    id="process_1",
                    flow_nodes=[
                        FlowNode(
                            id="start_1",
                            type=FlowNodeType.START_EVENT,
                            outgoing=["sf_1"],
                        ),
                        FlowNode(
                            id="task_1",
                            type=FlowNodeType.TASK,
                            incoming=["sf_1"],
                            outgoing=["sf_2"],
                        ),
                        FlowNode(
                            id="end_1", type=FlowNodeType.END_EVENT, incoming=["sf_2"]
                        ),
                    ],
                    sequence_flows=[
                        SequenceFlow(
                            id="sf_1", source_ref="start_1", target_ref="task_1"
                        ),
                        SequenceFlow(
                            id="sf_2", source_ref="task_1", target_ref="end_1"
                        ),
                    ],
                )
            ],
        )
        return RepairResult(
            repaired_diagram=repaired_diagram,
            unresolved=[
                UnresolvedRepair(rule_id="R999", reason="needs business context")
            ],
            rev_id="0002",
            session_id="session-456",
        )

    monkeypatch.setattr("app.cli.repair_diagram", fake_repair_diagram)

    invalid_bpmn = tmp_path / "invalid.bpmn"
    invalid_bpmn.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="defs" targetNamespace="http://bpmn.io/schema/bpmn">
  <process id="process_1" isExecutable="false">
    <task id="task_1" name="Standalone task" />
  </process>
</definitions>
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["repair", str(invalid_bpmn), "--json"])

    assert result.exit_code == 0
    assert '"repaired": true' in result.stdout
    assert '"prompt_version": "repair.txt"' in result.stdout
    assert '"rule_id": "R999"' in result.stdout


def test_batch_validate_command_supports_json_array_output(tmp_path: Path):
    invalid_bpmn = tmp_path / "invalid.bpmn"
    invalid_bpmn.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="defs" targetNamespace="http://bpmn.io/schema/bpmn">
  <process id="process_1" isExecutable="false">
    <task id="task_1" name="Standalone task" />
  </process>
</definitions>
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["batch-validate", str(tmp_path), "--format", "json"])

    assert result.exit_code == 0
    assert result.stdout.lstrip().startswith("[")
    assert '"metadata": {' in result.stdout


def test_batch_validate_command_supports_text_output(tmp_path: Path):
    invalid_bpmn = tmp_path / "invalid.bpmn"
    invalid_bpmn.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="defs" targetNamespace="http://bpmn.io/schema/bpmn">
  <process id="process_1" isExecutable="false">
    <task id="task_1" name="Standalone task" />
  </process>
</definitions>
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["batch-validate", str(tmp_path), "--format", "text"])

    assert result.exit_code == 0
    assert "INVALID" in result.stdout
    assert "METADATA" in result.stdout
