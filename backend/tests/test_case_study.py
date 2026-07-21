"""Pin the §6.1 case study diagram to the findings the thesis text describes.

The case study prose quotes specific rule ids against specific elements. If a
rule changes and this diagram stops producing them, the chapter is wrong and
nothing else in the suite would notice — data/test_cases has no other coverage.
"""
from pathlib import Path

from app.model.formats.pydantic_ir import PydanticConverter
from app.validation.checkers import run_woflan
from app.validation.rules import validate

CASE_STUDY = Path("data/test_cases/09_expense_reimbursement.bpmn")

# planted faults F1 and F2. see the diagram's own header comment for the stories
EXPECTED_TIER1 = {
    ("R003", "start_card_feed"),
    ("R004", "end_escalated"),
    ("R008", "task_audit"),
}


def _diagram():
    return PydanticConverter().parse(CASE_STUDY.read_bytes())


def test_case_study_round_trips():
    converter = PydanticConverter()
    diagram = _diagram()
    assert converter.parse(converter.serialize(diagram)) == diagram


def test_case_study_fires_the_expected_tier1_rules():
    issues = validate(_diagram()).issues
    assert {(i.rule_id, i.element_id) for i in issues} == EXPECTED_TIER1


def test_case_study_is_unsound():
    """F3: the parallel join at gw_close can never fire"""
    issues = run_woflan(_diagram())
    assert [i.rule_id for i in issues] == ["woflan:soundness"]
    assert issues[0].severity == "error"


def test_implicit_merge_is_not_flagged():
    """negative control: task_check's two incoming flows are legal, not a defect"""
    issues = validate(_diagram()).issues
    assert not any(issue.element_id == "task_check" for issue in issues)
