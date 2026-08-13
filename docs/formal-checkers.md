# Tier 2 formal validation

Tier 2 runs PM4Py Woflan in-process against the canonical diagram serialized
to BPMN XML. It complements the deterministic Tier 1 rules with workflow-net
soundness analysis and does not call an LLM.

## Execution

`app.validation.checkers.run_tier2_checkers` runs Woflan synchronously. PM4Py's
CPU-bound analysis is not reliably cancellable in a worker thread; dataset
probing therefore provides process isolation, while request validation runs in
the request process.

The checker is enabled in `backend/app/validation/checkers.yaml` and selected
per request with:

```json
{
  "tiers_enabled": {"t1": true, "t2": true, "t3": false}
}
```

## Output

A sound model produces no Tier 2 issue. An unsound model produces a normalized
`ValidationIssue` with:

- `rule_id = "woflan:soundness"`;
- stable BPMN IDs in `element_refs` where PM4Py diagnostics can be localized;
- dead and uncovered elements in `formal_witness`;
- localized locking scenarios in `counterexample_traces`;
- raw PM4Py names retained in `raw.petri_net_names` for traceability.

Unsupported BPMN node types produce `woflan:unsupported` with warning severity
instead of an unsoundness verdict. Runtime failures produce
`checker:woflan:runtime` and remain visible in the response.

Synthetic Petri-net nodes are filtered during localization. Findings are
deduplicated before they are merged with Tier 1 and Tier 3 results.

## Boundaries

The supported mapping covers start and end events, task variants, and
exclusive, inclusive, and parallel gateways. Collaborations and node types
outside that mapping do not receive a Woflan verdict. The evaluation probe
records this explicitly so an unsupported case cannot be counted as sound.

Checker versions are included in the `run.checkers` envelope. Formal evidence
can be withheld from repair prompts with `include_formal_evidence=false` while
keeping the checker verdict available.

## Integration points

- `backend/app/validation/checkers.py` — Woflan adapter and normalization.
- `backend/app/validation/checkers.yaml` — local enablement.
- `backend/app/services/validation.py` — Tier 1/2/3 orchestration.
- `backend/app/validation/rules.py` — shared issue and witness schemas.
