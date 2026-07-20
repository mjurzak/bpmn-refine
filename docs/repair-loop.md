# repair loop

Repair is one of the three top-level operations (see [`architecture.md`](architecture.md)). It takes **issues** (found by tier 1, tier 2, and/or tier 3) and a diagram, and produces **proposed changes** — never silent mutations. The user decides whether to apply them.

Internally, the repair endpoint is a **closed loop**: dispatcher -> apply -> re-validate -> stop-or-iterate. The loop is what makes repair more than a single-turn LLM call: it tightens convergence on a correct diagram, and it produces a record (`iterations`, `applied_ops`) that is useful both to the user and to Phase 3 evaluation.

**Status (2026-07-20):** the loop, both repair modes, the quick-fix registry (R001–R006), the `/repair`, `/repair/xml`, and `/repair/apply` endpoints, and the `tier2_findings` prompt section are all wired. The one gap is the *content* of the counterexample: the witness structure below is populated only with a diagnosis, not a firing trace, because the wired tier-2 checker (Woflan) does not emit one. The trace fields in the examples that follow are the target shape, not current output.

This doc owns:

- the `/repair` contract,
- the authoritative `EditOp` schema,
- the two repair modes (`atomic`, `regen`),
- the dispatcher (quick-fix vs LLM-guided),
- the counterexample-to-prompt pipeline,
- the convergence policy.

---

## contract

```
POST /api/v1/repair
```

Request:

```
{
  xml:            str,                  // current BPMN XML (server re-parses to canonical IR)
  issues:         Issue[],              // from tier 1/2/3 validation
  ExperimentConfig: { ... }             // see architecture.md
}
```

Response:

```
{
  updated_xml:       str,               // the proposed diagram, serialised back to BPMN XML
  applied_ops:       EditOp[],          // the sequence of ops actually applied to reach updated_xml
  remaining_issues:  Issue[],           // whatever the final tier-1 (and tier-2 if enabled) pass still reports
  iterations:        int,               // number of dispatcher iterations executed
  converged:         bool,              // true iff remaining_issues contains no errors
  run:               { ... }            // response envelope, see run.md
}
```

`/repair` is meaningless without an issue list. A frontend "Validate & Repair" button is a UI chain (`/validate` -> `/repair`); it is not a backend automation. Nothing is applied to the real diagram until the user accepts the response.

---

## `EditOp` schema

The atomic unit of diagram change. Every LLM-emitted edit plan is a list of these. Deterministic quick-fixes emit the same shape, so the server has one code path for applying changes.

```
EditOp = AddNode | RemoveNode | RenameElement | ChangeNodeType
       | AddFlow  | RemoveFlow | SetCondition  | ChangeGatewayType
       | AddEvent | AttachBoundaryEvent | ...    // extensible union
```

Every op has an `op` discriminator and references canonical-IR IDs:

```
AddNode {
  op: "add_node",
  id: str,                        // new node id; must be unique
  node_type: FlowNodeType,
  name?: str,
  process_id: str,                // parent process
}

RemoveNode {
  op: "remove_node",
  id: str,                        // existing node id; fails if references remain
  cascade?: bool,                 // if true, remove incident flows; default false
}

AddFlow {
  op: "add_flow",
  id: str,
  source_ref: str,
  target_ref: str,
  name?: str,
  condition_expression?: str,
}

RemoveFlow {
  op: "remove_flow",
  id: str,
}

RenameElement {
  op: "rename_element",
  id: str,
  new_name: str,
}

ChangeNodeType {
  op: "change_node_type",
  id: str,
  new_type: FlowNodeType,         // e.g. task -> userTask
}

ChangeGatewayType {
  op: "change_gateway_type",
  id: str,
  new_type: FlowNodeType,         // exclusiveGateway <-> parallelGateway etc.
}

SetCondition {
  op: "set_condition",
  flow_id: str,
  condition_expression: str | null,  // null clears the condition
}
```

### application semantics

- Ops apply **in order** to the canonical IR.
- An op that references a non-existent id, or would create a duplicate id, fails — the dispatcher rolls back the failed op and records it (future ops in the plan may still apply).
- Every successful op is appended to `applied_ops` in the response.
- The server re-runs tier 1 (and tier 2 if `tiers_enabled.t2`) after each dispatcher iteration — not after each op.

### structured-output constraints

Atomic LLM repair uses provider-level structured outputs with a top-level
`{"ops": [...]}` envelope. The schema is generated per repair request from the
current diagram so existing-reference fields are narrowed to the correct ID set:
node-targeting operations accept only current `node_ids`, flow-targeting
operations accept only current `flow_ids`, `add_flow.source_ref` /
`add_flow.target_ref` accept current node IDs, and `add_node.process_id` accepts
current process IDs. New IDs (`add_node.id`, `add_flow.id`) remain free strings
because they must not already exist; duplicate prevention is enforced by the
server when applying ops.

The same `id_constraints` object is included in the LLM payload for readability,
and the backend validates returned ops against the current diagram before
Pydantic conversion. This turns common schema-valid but domain-invalid plans
such as `remove_flow(id=<node id>)` into explicit repair-generation errors
instead of silent no-op proposals.

### why ops, not a full IR by default

Benchmarks from BPMN Assistant (Licardo et al., 2025) report roughly 43% lower latency and 75% fewer output tokens for atomic editing versus full-XML regeneration, with higher edit-success rates across Claude, GPT, and DeepSeek. More importantly, op-level edits are **localisable**: the frontend can show the user a compact diff, and the history service can record a meaningful revision reason.

---

## repair modes

Selected per request via `ExperimentConfig.repair_mode`:

| Mode | LLM output | When to use |
|---|---|---|
| **atomic** *(default)* | `EditOp[]` | targeted fixes driven by specific issues; default for all production flows |
| **regen** | full replacement canonical IR | large-scale rewrites; baseline for comparison experiments |

Under `regen`, the response shape is identical but `applied_ops` is a synthetic list of one op: `ReplaceDiagram`. This keeps the API uniform.

---

## dispatcher

```
input: diagram, issues[], ExperimentConfig
state: iteration = 0, applied = []

while iteration < ExperimentConfig.max_repair_iters:
    pick the highest-severity unresolved issue i
    if no issue left or all remaining are info-severity:
        break

    quick_fix = deterministic_fix_for(i)        # from the tier 1 category map + Analyzer 2.0 quick-fixes
    if quick_fix is not None:
        ops = [quick_fix]
    else:
        ops = llm_repair(
            diagram,
            issue = i,
            counterexample = i.counterexample,      # if any
            mode = ExperimentConfig.repair_mode,
        )

    diagram, op_results = apply(ops, diagram)
    applied += op_results.successful

    issues = revalidate(diagram, tiers = [1] + ([2] if cfg.tiers_enabled.t2 else []))
    iteration += 1

return diagram, applied, issues, iteration, converged = no_errors_in(issues)
```

### deterministic quick-fixes

Come from two sources:

1. **Tier 1 category map** ([`validation-rules.md`](validation-rules.md)) — e.g. R005/R006 -> remove a dangling sequence flow. Structural errors that need a human decision about *where* to wire (R001-R004, R007/R008) are surfaced as suggestions, not auto-applied.
2. **BPMN Analyzer 2.0** ([`formal-checkers.md`](formal-checkers.md)) — bundles quick-fixes for several soundness/safeness violations (mismatched gateway types, untriggered message events, unsafe sequence flows). These are lifted into `EditOp[]` by the tier-2 adapter.

The dispatcher prefers a deterministic fix whenever one is available, even if the LLM could also do it — deterministic fixes are cheaper, verifiable, and always reproduce.

### LLM-guided repair

Invoked when no deterministic fix exists for an issue. The prompt carries the counterexample (if tier 2 produced one), the canonical IR in the active candidate format, and the target `EditOp` schema. See [`llm-integration.md`](llm-integration.md) for the prompt layout.

---

## counterexample plumbing

When the dispatched issue came from tier 2 with a `counterexample`, the server builds a structured prompt section:

```
## problem
Rule: analyzer:option-to-complete
Severity: error
Message: Deadlock reached after firing {task_A, gateway_G}
Affected elements: task_A, gateway_G, task_B

## counterexample
Kind: deadlock
Trace:
  step 0: (start) -> token at start
  step 1: start_event_fires -> token at seq_1
  step 2: exclusive_gateway_G -> tokens at seq_2, seq_3
  step 3: [DEADLOCK] task_B waiting on join
Marking at failure: { task_B_join: 1, seq_4: 0 }

## canonical IR (active format: yaml)
... diagram body ...

## task
Emit an EditOp[] that fixes the deadlock at task_B_join without
altering branches unrelated to gateway_G.
```

The LLM reasons about *why* the trace deadlocks (the classic exclusive-split / parallel-join mismatch) and emits a targeted fix (e.g. `change_gateway_type` on `G`). This is the neuro-symbolic step the thesis rests on — structured counterexample -> localised repair, not regenerate-everything-and-hope.

---

## convergence and iteration bound

- `ExperimentConfig.max_repair_iters` caps the outer loop. Default: **5**.
- The loop stops early if remaining issues contain no errors (warnings and infos are acceptable).
- `iterations` is written to `run.iterations` so experiments can report convergence behaviour against the 4/δ theoretical bound (Dantas et al., 2025; see Initial-Research.md §4).
- A non-converged result is still returned — the user sees a partial repair and decides whether to accept, refine in chat, or try a different repair mode.

---

## user acceptance

The frontend presents the response as a **proposed change**, not an applied one:

- `ApplyChanges` button — commits the proposal to the current bpmn-js diagram and creates a history revision.
- `Reject` — discards the proposal entirely; history is unaffected.
- `Refine in chat` — opens `/chat` pre-seeded with the current diagram and the proposal, so the user can ask for adjustments before deciding.

Until the user clicks `ApplyChanges`, nothing in the canonical IR or history changes. This is the system-level enforcement of "no silent mutations."

---

## metrics the loop produces

For Phase 3 evaluation, each repair run contributes:

- `iterations` — convergence speed.
- `len(applied_ops)` — minimality of the repair.
- `converged` — hit-rate against the test set.
- `ged(initial, final)` and `ged(final, ground_truth)` — how close the repair got to the expected fix.
- `wall_clock_ms` per iteration — latency budget check.

All of these are surfaced via `run` or the response body and are analysed from log data rather than by instrumenting the dispatcher further. See [`experiments.md`](experiments.md).
