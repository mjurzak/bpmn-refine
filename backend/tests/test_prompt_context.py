from app.experiments import ExperimentConfig, IrFormat, RepairMode
from app.llm.prompt_context import render_prompt_template
from app.model.schema import FlowNodeType
from app.services.chat import chat_prompt_path
from app.services.repair import repair_prompt_path
from app.services.validation import validate_prompt_path


def test_chat_prompt_renders_current_ir_schema_and_flow_node_types():
    rendered = render_prompt_template(chat_prompt_path(), config=ExperimentConfig())

    assert "{{" not in rendered
    assert "BpmnDiagram" in rendered
    assert "FlowNodeType" in rendered
    for node_type in FlowNodeType:
        assert node_type.value in rendered


def test_atomic_repair_prompt_renders_atomic_edit_op_schema():
    rendered = render_prompt_template(
        repair_prompt_path(ExperimentConfig(repair_mode=RepairMode.ATOMIC)),
        config=ExperimentConfig(repair_mode=RepairMode.ATOMIC),
    )

    assert "{{" not in rendered
    assert "add_node" in rendered
    assert "set_condition" in rendered
    assert "replace_diagram" not in rendered


def test_regen_repair_prompt_renders_requested_ir_format():
    config = ExperimentConfig(repair_mode=RepairMode.REGEN, ir_format=IrFormat.YAML)

    rendered = render_prompt_template(repair_prompt_path(config), config=config)

    assert "Requested IR format: `yaml`" in rendered
    assert "{{IR_CONTEXT}}" not in rendered


def test_validation_prompt_does_not_duplicate_the_input_ir_schema():
    rendered = render_prompt_template(validate_prompt_path(), config=ExperimentConfig())

    assert "Requested IR format: `pydantic`" in rendered
    assert '"$defs"' not in rendered
    assert "result.findings" in rendered
    assert "reference_evidence" in rendered
    assert "classification_basis" in rendered
    assert "semantic_view" in rendered


def test_full_edit_op_schema_can_be_rendered_for_backend_contract(tmp_path):
    template = tmp_path / "edit_ops_prompt.txt"
    template.write_text("{{EDIT_OP_SCHEMA}}")

    rendered = render_prompt_template(template, config=ExperimentConfig())

    assert "add_node" in rendered
    assert "replace_diagram" in rendered
