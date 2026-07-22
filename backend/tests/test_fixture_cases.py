"""Pin the two remaining manual fixtures in data/test_cases.

Case 03 has its own module (test_case_study.py) because the thesis quotes it.
These two are demonstration fixtures — they exist to be loaded in the browser —
but each one carries a claim the UI makes on its behalf, and until now nothing
in the suite checked that the claim was still true.
"""
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
    """the point of this fixture: an XOR split feeding an AND join is legal syntax

    Every tier-1 rule is an error and firing has to guarantee a defect, so none of
    them may fire here. If one starts to, the invariant broke — not the fixture.
    """
    assert validate(_parse(GATEWAY_ISSUES)).issues == []


def test_gateway_case_deadlocks_under_tier2():
    """gw_notify waits for both branches, gw_approval only ever takes one"""
    issues = run_woflan(_parse(GATEWAY_ISSUES))
    assert [i.rule_id for i in issues] == ["woflan:soundness"]
    assert issues[0].severity == "error"
    # localisation must stay in BPMN terms, never leak Petri-net place names
    assert set(issues[0].element_refs) == {"task_notify", "gw_approval", "f5", "f6"}


def test_duplicate_ids_case_is_rejected_at_parse():
    """no validation pass runs on this one — construction fails first, by design

    Reporting a duplicate after the fact is impossible: the adjacency index has
    already collapsed the two nodes into one.
    """
    with pytest.raises(ValueError, match="task_resolve"):
        _parse(DUPLICATE_IDS)
