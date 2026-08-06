"""Pin the two demonstration fixtures in data/test_cases (case 03 lives in test_case_study.py)."""
from pathlib import Path

import pytest

from app.model.formats.pydantic_ir import PydanticConverter
from app.validation.checkers import run_woflan
from app.validation.rules import validate

GATEWAY_ISSUES = Path("data/test_cases/01_gateway_issues.bpmn")
DUPLICATE_IDS = Path("data/test_cases/02_duplicate_ids.bpmn")


def _parse(path: Path):
    return PydanticConverter().parse(path.read_bytes())


def test_gateway_case_round_trips():
    converter = PydanticConverter()
    diagram = _parse(GATEWAY_ISSUES)
    assert converter.parse(converter.serialize(diagram)) == diagram


def test_gateway_case_is_invisible_to_tier1():
    """An XOR split feeding an AND join is legal syntax, so no tier-1 rule may fire."""
    assert validate(_parse(GATEWAY_ISSUES)).issues == []


def test_gateway_case_deadlocks_under_tier2():
    """gw_notify waits for both branches, gw_approval only ever takes one."""
    issues = run_woflan(_parse(GATEWAY_ISSUES))
    assert [i.rule_id for i in issues] == ["woflan:soundness"]
    assert issues[0].severity == "error"
    # localisation stays in BPMN terms, no Petri-net place names
    assert set(issues[0].element_refs) == {"task_notify", "gw_approval", "f5", "f6"}


def test_duplicate_ids_case_is_rejected_at_parse():
    """Construction fails first, so no validation pass ever runs on this one."""
    with pytest.raises(ValueError, match="task_resolve"):
        _parse(DUPLICATE_IDS)
