"""Validate the data/rule_cases fixtures against their expected issues.

Each rule gets a `.broken` fixture that isolates exactly one rule and a `.fixed`
counterpart that is the repaired model. This keeps the rule->example mapping
honest: if a rule changes, its fixture either still fires the expected id or the
test fails loudly. The `.fixed` half doubles as repair ground truth — whatever
the dispatcher produces for the broken input should be equivalent to it.

All fixtures share one mnemonic theme (a pizza order) so the shape of a defect is
what differs between them, not the vocabulary.
"""
from pathlib import Path

import pytest

from app.model.formats.pydantic_ir import PydanticConverter
from app.validation.rules import validate

# every rule with a fixture pair; the broken half must fire exactly {rule}, the
# fixed half must be clean
PAIRED_RULES: dict[str, str] = {
    "R001": "R001_no_start_event",
    "R002": "R002_no_end_event",
    "R003": "R003_start_without_outgoing",
    "R004": "R004_end_without_incoming",
    "R005": "R005_unknown_source_ref",
    "R006": "R006_unknown_target_ref",
    "R007": "R007_unreachable_from_start",
    "R008": "R008_cannot_reach_end",
}

# fixtures that stand alone rather than in a broken/fixed pair
UNPAIRED: dict[str, set[str]] = {
    "R000_valid_baseline.bpmn": set(),
}

CASES_DIR = Path("data/rule_cases")


def _fired(filename: str) -> set[str]:
    diagram = PydanticConverter().parse((CASES_DIR / filename).read_bytes())
    return {issue.rule_id for issue in validate(diagram).issues}


@pytest.mark.parametrize("rule, stem", sorted(PAIRED_RULES.items()))
def test_broken_fixture_fires_exactly_its_rule(rule: str, stem: str):
    assert _fired(f"{stem}.broken.bpmn") == {rule}


@pytest.mark.parametrize("rule, stem", sorted(PAIRED_RULES.items()))
def test_fixed_fixture_is_clean(rule: str, stem: str):
    assert _fired(f"{stem}.fixed.bpmn") == set()


@pytest.mark.parametrize("filename, expected", sorted(UNPAIRED.items()))
def test_unpaired_fixture_fires_expected_rules(filename: str, expected: set[str]):
    assert _fired(filename) == expected


def test_every_fixture_is_covered():
    on_disk = {p.name for p in CASES_DIR.glob("*.bpmn")}
    accounted = set(UNPAIRED) | {
        f"{stem}.{half}.bpmn" for stem in PAIRED_RULES.values() for half in ("broken", "fixed")
    }
    assert on_disk == accounted, "a fixture exists with no expected-issue entry (or vice versa)"


def test_every_rule_in_the_engine_has_a_fixture():
    """RULES_VERSION names the rule range; every id in it needs a worked example"""
    from app.validation.rules import RULES_VERSION

    first, last = RULES_VERSION.split("-")
    expected = {f"R{n:03d}" for n in range(int(first[1:]), int(last[1:]) + 1)}
    assert set(PAIRED_RULES) == expected
