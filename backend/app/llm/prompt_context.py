"""Dynamic context rendering for versioned LLM prompt templates."""

from __future__ import annotations

import json
from pathlib import Path

from app.experiments import ExperimentConfig
from app.model.schema import BpmnDiagram, FlowNodeType
from app.repair.ops import atomic_edit_op_list_adapter, edit_op_list_adapter


def render_prompt_template(path: Path, config: ExperimentConfig | None = None) -> str:
    """render a prompt template with schema/context placeholders filled in"""
    template = path.read_text()
    replacements = {
        "{{IR_CONTEXT}}": _ir_context(config),
        "{{BPMN_IR_SCHEMA}}": _bpmn_ir_schema(),
        "{{FLOW_NODE_TYPES}}": _flow_node_types(),
        "{{ATOMIC_EDIT_OP_SCHEMA}}": _atomic_edit_op_schema(),
        "{{EDIT_OP_SCHEMA}}": _edit_op_schema(),
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template


def _ir_context(config: ExperimentConfig | None) -> str:
    active_config = config or ExperimentConfig()
    return (
        f"Requested IR format: `{active_config.ir_format}`.\n"
        "The request envelope carries `ir_format` and a `diagram` field. "
        "For `pydantic`, `diagram` is the canonical BPMN IR as a JSON object. "
        "For candidate formats, `diagram` is a string encoded in the selected "
        "format and responses must return the same format."
    )


def _bpmn_ir_schema() -> str:
    return _json_block(BpmnDiagram.model_json_schema())


def _flow_node_types() -> str:
    return ", ".join(item.value for item in FlowNodeType)


def _atomic_edit_op_schema() -> str:
    return _json_block(atomic_edit_op_list_adapter.json_schema())


def _edit_op_schema() -> str:
    return _json_block(edit_op_list_adapter.json_schema())


def _json_block(value: object) -> str:
    return "```json\n" + json.dumps(value, indent=2, sort_keys=True) + "\n```"
