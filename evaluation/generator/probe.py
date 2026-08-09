"""Eligibility probe — stage one of dataset generation (METHODOLOGY §2).

A model may serve as a seed only when the importer keeps all of it, the IR
survives a round trip, tier 1 reports nothing, and tier 2 returns sound. Every
other model is excluded with the reason recorded: a corpus filtered by an
unrecorded criterion cannot be audited, and the models that fail here are
themselves a finding about parser coverage.

The probe never calls a model provider, so it is free and deterministic.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
import multiprocessing
from pathlib import Path
from multiprocessing.connection import Connection

from app.experiments import ExperimentConfig, T2Tool, TiersEnabled, hash_bytes
from app.model.registry import get_converter
from app.model.schema import BpmnDiagram
from app.services.validation import validate_diagram
from app.validation.rules import Severity, ValidationIssue, ValidationTier
from pydantic import BaseModel, Field

PROBE_VERSION = "1.1"


class Verdict(StrEnum):
	"""why a model is or is not a seed — one reason per model, first failure wins"""

	ELIGIBLE = "eligible"
	PARSE_ERROR = "parse_error"
	LOSSY_IMPORT = "lossy_import"
	ROUND_TRIP_FAILED = "round_trip_failed"
	TIER1_FINDINGS = "tier1_findings"
	TIER2_UNSOUND = "tier2_unsound"
	TIER2_UNSUPPORTED = "tier2_unsupported"
	TIER2_ERROR = "tier2_error"


class ProbeRecord(BaseModel):
	"""the complete verdict on one source model"""

	seed: str
	source_path: str
	source_hash: str | None = None
	verdict: Verdict
	detail: str | None = None
	dropped: list[str] = Field(default_factory=list)
	tier1_findings: list[str] = Field(default_factory=list)
	tier2_findings: list[str] = Field(default_factory=list)
	node_count: int | None = None
	flow_count: int | None = None
	has_description: bool = False

	@property
	def eligible(self) -> bool:
		return self.verdict is Verdict.ELIGIBLE


class ProbeReport(BaseModel):
	probe_version: str = PROBE_VERSION
	source_dir: str
	records: list[ProbeRecord]

	@property
	def seeds(self) -> list[ProbeRecord]:
		return [record for record in self.records if record.eligible]

	def counts(self) -> dict[str, int]:
		tally: dict[str, int] = {}
		for record in self.records:
			tally[record.verdict.value] = tally.get(record.verdict.value, 0) + 1
		return tally


# tier 3 is an LLM call and plays no part in the seed gate
_GATE_CONFIG = ExperimentConfig(
	tiers_enabled=TiersEnabled(t1=True, t2=True, t3=False),
	t2_tools=[T2Tool.WOFLAN],
)


async def probe_model(
	path: Path,
	description_path: Path | None = None,
	config: ExperimentConfig = _GATE_CONFIG,
) -> ProbeRecord:
	"""run the full seed gate against one BPMN file"""
	seed = path.stem
	record = ProbeRecord(
		seed=seed,
		source_path=str(path),
		verdict=Verdict.ELIGIBLE,
		has_description=bool(description_path and description_path.exists()),
	)

	try:
		payload = path.read_bytes()
		record.source_hash = hash_bytes(payload)
		diagram, unsupported = get_converter().parse_with_diagnostics(payload)
	except Exception as exc:
		return record.model_copy(
			update={
				"verdict": Verdict.PARSE_ERROR,
				"detail": f"{type(exc).__name__}: {exc}",
			}
		)

	record.node_count = sum(len(proc.flow_nodes) for proc in diagram.processes)
	record.flow_count = sum(len(proc.sequence_flows) for proc in diagram.processes)

	if unsupported:
		record.dropped = sorted({element.tag for element in unsupported})
		return record.model_copy(
			update={
				"verdict": Verdict.LOSSY_IMPORT,
				"detail": (
					f"{len(unsupported)} child element(s) the IR does not represent: "
					+ ", ".join(record.dropped)
				),
			}
		)

	round_trip_error = _round_trip_error(diagram)
	if round_trip_error:
		return record.model_copy(
			update={"verdict": Verdict.ROUND_TRIP_FAILED, "detail": round_trip_error}
		)

	result = await validate_diagram(diagram, config=config)
	tier1 = [i for i in result.issues if i.tier == ValidationTier.TIER1]
	tier2 = [i for i in result.issues if i.tier == ValidationTier.TIER2]
	record.tier1_findings = [issue.rule_id for issue in tier1]
	record.tier2_findings = [issue.rule_id for issue in tier2]

	if tier1:
		return record.model_copy(
			update={
				"verdict": Verdict.TIER1_FINDINGS,
				"detail": _describe(tier1),
			}
		)

	# a checker that crashed says nothing about soundness, so it is its own verdict
	runtime_errors = [issue for issue in tier2 if issue.rule_id.endswith(":runtime_error")]
	if runtime_errors:
		return record.model_copy(
			update={"verdict": Verdict.TIER2_ERROR, "detail": _describe(runtime_errors)}
		)

	unsupported = [issue for issue in tier2 if issue.rule_id.endswith(":unsupported")]
	if unsupported:
		return record.model_copy(
			update={
				"verdict": Verdict.TIER2_UNSUPPORTED,
				"detail": _describe(unsupported),
			}
		)

	unsound = [issue for issue in tier2 if issue.severity == Severity.ERROR]
	if unsound:
		return record.model_copy(
			update={"verdict": Verdict.TIER2_UNSOUND, "detail": _describe(unsound)}
		)

	return record


async def probe_corpus(
	source_dir: Path,
	description_dir: Path | None = None,
	config: ExperimentConfig = _GATE_CONFIG,
) -> ProbeReport:
	"""probe every `.bpmn` file in `source_dir`, in sorted order"""
	records: list[ProbeRecord] = []
	for path in sorted(source_dir.glob("*.bpmn")):
		description = (
			description_dir / f"{path.stem}.txt" if description_dir else None
		)
		records.append(await probe_model(path, description, config=config))
	return ProbeReport(source_dir=str(source_dir), records=records)


def _round_trip_error(diagram: BpmnDiagram) -> str | None:
	"""check that exported bytes re-parse to the same IR"""
	converter = get_converter()
	try:
		reparsed = converter.parse(converter.serialize(diagram))
	except Exception as exc:
		return f"re-parse of exported XML failed: {type(exc).__name__}: {exc}"
	stripped = _without_serialiser_additions(reparsed, diagram)
	if stripped != diagram:
		return _first_difference(diagram, stripped)
	return None


def _without_serialiser_additions(
	reparsed: BpmnDiagram, source: BpmnDiagram
) -> BpmnDiagram:
	"""drop what the serialiser adds by design: layout for unplaced elements and the namespace declarations BPMNDI needs"""
	stripped = reparsed.model_copy(deep=True)
	stripped.namespaces = dict(source.namespaces)
	for original, restored in zip(source.processes, stripped.processes):
		unplaced = {node.id for node in original.flow_nodes if node.bounds is None}
		unlabelled = {
			node.id for node in original.flow_nodes if node.label_bounds is None
		}
		for node in restored.flow_nodes:
			if node.id in unplaced:
				node.bounds = None
			if node.id in unlabelled:
				node.label_bounds = None
		unrouted = {flow.id for flow in original.sequence_flows if not flow.waypoints}
		unlabelled_flows = {
			flow.id for flow in original.sequence_flows if flow.label_bounds is None
		}
		for flow in restored.sequence_flows:
			if flow.id in unrouted:
				flow.waypoints = []
			if flow.id in unlabelled_flows:
				flow.label_bounds = None
	return stripped


def _first_difference(before: BpmnDiagram, after: BpmnDiagram) -> str:
	"""name one field that changed, so the record says more than 'not equal'"""
	if len(before.processes) != len(after.processes):
		return (
			f"process count changed {len(before.processes)} -> {len(after.processes)}"
		)
	for original, restored in zip(before.processes, after.processes):
		if len(original.flow_nodes) != len(restored.flow_nodes):
			return (
				f"process '{original.id}' flow node count "
				f"{len(original.flow_nodes)} -> {len(restored.flow_nodes)}"
			)
		if len(original.sequence_flows) != len(restored.sequence_flows):
			return (
				f"process '{original.id}' sequence flow count "
				f"{len(original.sequence_flows)} -> {len(restored.sequence_flows)}"
			)
		for node, restored_node in zip(original.flow_nodes, restored.flow_nodes):
			if node != restored_node:
				return f"flow node '{node.id}' differs after export"
		for flow, restored_flow in zip(original.sequence_flows, restored.sequence_flows):
			if flow != restored_flow:
				return f"sequence flow '{flow.id}' differs after export"
	return "diagram differs after export"


def _describe(issues: list[ValidationIssue]) -> str:
	return "; ".join(f"{issue.rule_id}: {issue.message}" for issue in issues)


def run_probe(
	source_dir: Path,
	description_dir: Path | None = None,
	config: ExperimentConfig = _GATE_CONFIG,
) -> ProbeReport:
	"""Run each model in an isolated process so timed-out Woflan threads die."""
	context = multiprocessing.get_context("spawn")
	records: list[ProbeRecord] = []
	for path in sorted(source_dir.glob("*.bpmn")):
		description = description_dir / f"{path.stem}.txt" if description_dir else None
		receive, send = context.Pipe(duplex=False)
		process = context.Process(
			target=_probe_model_worker,
			args=(send, path, description, config),
		)
		process.start()
		send.close()
		if not receive.poll(30):
			process.terminate()
			process.join()
			raise TimeoutError(f"probe worker did not return for {path.name}")
		records.append(ProbeRecord.model_validate(receive.recv()))
		receive.close()
		if process.is_alive():
			process.terminate()
		process.join()
	return ProbeReport(source_dir=str(source_dir), records=records)


def _probe_model_worker(
	connection: Connection,
	path: Path,
	description: Path | None,
	config: ExperimentConfig,
) -> None:
	loop = asyncio.new_event_loop()
	try:
		record = loop.run_until_complete(probe_model(path, description, config))
		connection.send(record.model_dump(mode="json"))
	finally:
		connection.close()
