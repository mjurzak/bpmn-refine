"""The seed gate must reject for the right reason, not just reject."""

from __future__ import annotations

from pathlib import Path

import pytest
from evaluation.generator.exclusions import render_exclusions, render_seed_list
from evaluation.generator.probe import Verdict, probe_corpus, probe_model

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"


def _definitions(body: str, extra: str = "") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="{BPMN_NS}" id="defs_1" targetNamespace="http://bpmn.io/schema/bpmn">
  {extra}
  <process id="Process_1" isExecutable="false">
{body}
  </process>
</definitions>
""".encode()


def _write(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / f"{name}.bpmn"
    path.write_bytes(payload)
    return path


SOUND = _definitions(
    """    <startEvent id="Start_1" />
    <task id="Task_1" name="Do the work" />
    <endEvent id="End_1" />
    <sequenceFlow id="Flow_1" sourceRef="Start_1" targetRef="Task_1" />
    <sequenceFlow id="Flow_2" sourceRef="Task_1" targetRef="End_1" />"""
)

NO_START = _definitions(
    """    <task id="Task_1" name="Do the work" />
    <endEvent id="End_1" />
    <sequenceFlow id="Flow_2" sourceRef="Task_1" targetRef="End_1" />"""
)

# an XOR split closed by an AND join — one branch fires, the join waits forever
DEADLOCK = _definitions(
    """    <startEvent id="Start_1" />
    <exclusiveGateway id="Split_1" />
    <task id="Task_A" name="A" />
    <task id="Task_B" name="B" />
    <parallelGateway id="Join_1" />
    <endEvent id="End_1" />
    <sequenceFlow id="Flow_1" sourceRef="Start_1" targetRef="Split_1" />
    <sequenceFlow id="Flow_2" sourceRef="Split_1" targetRef="Task_A" />
    <sequenceFlow id="Flow_3" sourceRef="Split_1" targetRef="Task_B" />
    <sequenceFlow id="Flow_4" sourceRef="Task_A" targetRef="Join_1" />
    <sequenceFlow id="Flow_5" sourceRef="Task_B" targetRef="Join_1" />
    <sequenceFlow id="Flow_6" sourceRef="Join_1" targetRef="End_1" />"""
)

WITH_COLLABORATION = _definitions(
    """    <startEvent id="Start_1" />
    <task id="Task_1" name="Do the work" />
    <endEvent id="End_1" />
    <sequenceFlow id="Flow_1" sourceRef="Start_1" targetRef="Task_1" />
    <sequenceFlow id="Flow_2" sourceRef="Task_1" targetRef="End_1" />""",
    extra='<collaboration id="Collab_1"><participant id="Part_1" processRef="Process_1" /></collaboration>',
)


async def test_a_clean_sound_model_is_a_seed(tmp_path: Path):
    record = await probe_model(_write(tmp_path, "ok", SOUND))

    assert record.verdict is Verdict.ELIGIBLE
    assert record.eligible
    assert record.node_count == 3
    assert record.flow_count == 2
    assert record.source_hash


async def test_a_tier1_finding_disqualifies_and_names_the_rule(tmp_path: Path):
    record = await probe_model(_write(tmp_path, "no_start", NO_START))

    assert record.verdict is Verdict.TIER1_FINDINGS
    assert "R001" in record.tier1_findings
    assert "R001" in (record.detail or "")


async def test_an_unsound_model_is_excluded_by_tier2(tmp_path: Path):
    record = await probe_model(_write(tmp_path, "deadlock", DEADLOCK))

    assert record.verdict is Verdict.TIER2_UNSOUND
    assert record.tier1_findings == []
    assert "woflan:soundness" in record.tier2_findings


async def test_dropped_children_disqualify_and_are_named(tmp_path: Path):
    record = await probe_model(_write(tmp_path, "collab", WITH_COLLABORATION))

    assert record.verdict is Verdict.LOSSY_IMPORT
    assert record.dropped == ["collaboration"]


async def test_unreadable_xml_is_a_parse_error_not_a_crash(tmp_path: Path):
    record = await probe_model(_write(tmp_path, "broken", b"<definitions"))

    assert record.verdict is Verdict.PARSE_ERROR
    assert record.detail


async def test_a_missing_description_is_recorded(tmp_path: Path):
    source = _write(tmp_path, "ok", SOUND)
    described = tmp_path / "ok.txt"

    without = await probe_model(source, described)
    described.write_text("The worker does the work.\n")
    with_text = await probe_model(source, described)

    assert not without.has_description
    assert with_text.has_description


async def test_the_corpus_report_counts_every_verdict(tmp_path: Path):
    _write(tmp_path, "01", SOUND)
    _write(tmp_path, "02", NO_START)
    _write(tmp_path, "03", WITH_COLLABORATION)

    report = await probe_corpus(tmp_path)

    assert [record.seed for record in report.records] == ["01", "02", "03"]
    assert report.counts() == {
        Verdict.ELIGIBLE.value: 1,
        Verdict.TIER1_FINDINGS.value: 1,
        Verdict.LOSSY_IMPORT.value: 1,
    }
    assert [record.seed for record in report.seeds] == ["01"]


async def test_exclusions_document_every_rejected_model(tmp_path: Path):
    _write(tmp_path, "01", SOUND)
    _write(tmp_path, "02", NO_START)
    _write(tmp_path, "03", WITH_COLLABORATION)

    markdown = render_exclusions(await probe_corpus(tmp_path))

    assert "**02**" in markdown
    assert "**03**" in markdown
    assert "**01**" not in markdown
    assert "1 of 3 models passed" in markdown


async def test_the_seed_list_holds_only_seeds(tmp_path: Path):
    _write(tmp_path, "01", SOUND)
    _write(tmp_path, "02", NO_START)

    assert render_seed_list(await probe_corpus(tmp_path)) == "01\n"


@pytest.mark.parametrize("payload", [SOUND, DEADLOCK, WITH_COLLABORATION])
async def test_probing_the_same_file_twice_gives_the_same_verdict(
    tmp_path: Path, payload: bytes
):
    """the gate decides membership, so a rerun that disagrees would rewrite the dataset"""
    source = _write(tmp_path, "case", payload)

    first = await probe_model(source)
    second = await probe_model(source)

    assert first == second
