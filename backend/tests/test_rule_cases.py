"""Validate the data/rule_cases fixtures against their expected issues.

Each fixture in data/rule_cases/ isolates one rule. This keeps the rule->example
mapping honest: if a rule changes, its fixture either still fires the expected id
or the test fails loudly.
"""
from pathlib import Path

import pytest

from app.model.formats.pydantic_ir import PydanticConverter
from app.validation.rules import validate

# the set of rule ids each fixture is expected to fire (empty == clean)
EXPECTED: dict[str, set[str]] = {
    "R000_valid_baseline.bpmn": set(),
    "R001_no_start_event.bpmn": {"R001"},
    "R002_no_end_event.bpmn": {"R002"},
    "R003_start_without_outgoing.bpmn": {"R003"},
    "R004_end_without_incoming.bpmn": {"R004"},
    "R005_unknown_source_ref.bpmn": {"R005"},
    "R006_unknown_target_ref.bpmn": {"R006"},
    "R007_unreachable_from_start.bpmn": {"R007"},
    "R008_cannot_reach_end.bpmn": {"R008"},
}

CASES_DIR = Path("data/rule_cases")


def _fired(filename: str) -> set[str]:
    diagram = PydanticConverter().parse((CASES_DIR / filename).read_bytes())
    return {issue.rule_id for issue in validate(diagram).issues}


@pytest.mark.parametrize("filename, expected", sorted(EXPECTED.items()))
def test_fixture_fires_expected_rules(filename: str, expected: set[str]):
    assert _fired(filename) == expected


def test_every_fixture_is_covered():
    on_disk = {p.name for p in CASES_DIR.glob("*.bpmn")}
    assert on_disk == set(EXPECTED), "a fixture exists with no expected-issue entry (or vice versa)"
