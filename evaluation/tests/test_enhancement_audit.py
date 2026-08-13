from __future__ import annotations

from pathlib import Path

from evaluation.enhancement_audit import audit_cases, load_cases


def test_real_enhancement_cases_pass_automatic_audit_without_tier2():
    cases = load_cases(Path("data/eval/enhancement/cases.json"))
    summary = audit_cases(cases, root=Path("data/eval/enhancement"), tier2="off")
    payload = summary.as_dict()
    assert payload["counts"]["cases"] == 21
    assert payload["counts"]["automatic_fail"] == 0
    assert "manual_status_counts" not in payload
    assert all(case["automatic_status"] == "pass_with_skips" for case in payload["cases"])
