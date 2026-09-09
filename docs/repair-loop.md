# repair loop

Repair is one of the three top-level operations (see [`architecture.md`](architecture.md)). It takes **issues** (found by tier 1, tier 2, and/or tier 3) and a diagram, and produces **proposed changes** — never silent mutations. The user decides whether to apply them.

The endpoint has two orchestration modes.

**Single plan** (`single_plan=true`, the default) backs manual approval: dispatcher -> apply to an isolated candidate -> re-validate -> return for review. Findings discovered on the way are reported, not repaired inside the same request.

**Closed loop** (`single_plan=false`) backs unattended experiment runs: dispatcher -> apply -> re-validate -> stop or iterate. Keeping it opt-in is what stops the human review gate from ending up behind several model-generated plans.

**Status (2026-09-03):** the loop, both repair modes, quick fixes for R001,
R002, R005, and R006, the `/repair`, `/repair/xml`, and `/repair/apply`
endpoints, and the `tier2_findings` prompt section are wired. Woflan locking
scenarios are localized to BPMN element IDs when available and populate
`counterexample_traces`; a separate deadlock marking is not currently derived.

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
  config:         ExperimentConfig,     // see architecture.md
  single_plan:    bool                  // default true; false for explicit closed-loop repair
}
```

Response:

```
{
  updated_xml:       str,               // the proposed diagram, serialised back to BPMN XML
  applied_ops:       EditOp[],          // the sequence of ops actually applied to reach updated_xml
  applied_op_origins: str[],            // per applied op: "quick_fix" | "model_plan" | "model_regen"
  remaining_issues:  Issue[],           // whatever the final tier-1 (and tier-2 if enabled) pass still reports
  iterations:        int,               // number of dispatcher iterations executed
  converged:         bool,              // true iff no repairable error or warning remains
  single_plan:       bool,              // orchestration mode used for this response
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
  process_id: str,               // process containing both endpoints
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
- Every successful op is appended to `applied_ops` in the response, and its producer to `applied_op_origins` at the same index — a deterministic quick fix and a model plan are otherwise indistinguishable once applied. `failed_ops` is annotated the same way.
- The server re-runs tier 1 (and tier 2 if `tiers_enabled.t2`) after each dispatcher iteration — not after each op.

### structured-output constraints

Atomic LLM repair uses provider-level structured outputs with the common `{"description": "...", "result": {"ops": [...]}}` envelope. The schema is regenerated per request from the current diagram, so every field that references an existing element is narrowed to the IDs that actually exist:

| Field | Restricted to |
|---|---|
| node-targeting op `id` | current `node_ids` |
| flow-targeting op `id` | current `flow_ids` |
| `add_flow.source_ref`, `add_flow.target_ref` | current node IDs |
| `add_node.process_id`, `add_flow.process_id` | current process IDs |

New IDs (`add_node.id`, `add_flow.id`) stay free strings, since the whole point is that they do not exist yet; the server rejects duplicates when it applies the ops.

The same `id_constraints` object goes into the LLM payload as readable text, and the backend re-checks returned ops against the diagram before Pydantic conversion. A plan like `remove_flow(id=<node id>)` is schema-valid but nonsense, and this is what turns it into an explicit repair-generation error rather than a silent no-op.

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

With `single_plan=true`, the highest-priority current batch is assigned to one
coherent plan: all errors when any exist, otherwise all warnings. The candidate
is revalidated once. Remaining or newly discovered findings are returned to the
reviewer instead of becoming more repair work inside the same request.

With `single_plan=false`, errors are drained before warnings and the dispatcher
may continue until convergence or `max_repair_iters`. The loop policy is explicit:

- omitted / `legacy_all_findings` preserves the historical E7 behavior and lets
  every revalidation replace the next repair scope;
- `target_scoped_safe` freezes the initially assigned targets, treats newly
  observed findings as context only, stops when those targets disappear, and
  rolls back a candidate that introduces a new deterministic Tier 1 or Woflan
  error. Rejected candidates are returned in `rejected_ops` with
  `regression_issues`; there is no automatic retry.

```
input: diagram, issues[], ExperimentConfig, single_plan
state: iteration = 0, applied = []

limit = 1 if single_plan else ExperimentConfig.max_repair_iters

while iteration < limit:
    assigned = highest_severity_batch
    if assigned is empty:
        break

    quick_fixes = deterministic_fixes_for(assigned)
    if quick_fixes cover every assigned issue:
        ops = quick_fixes
    else:
        ops = llm_repair(
            diagram,
            issues = assigned,
            mode = ExperimentConfig.repair_mode,
        )

    diagram, op_results = apply(ops, diagram)
    applied += op_results.successful

    issues = revalidate(diagram, configured_tiers)
    iteration += 1

return diagram, applied, issues, iteration, converged = no_repairable_issues(issues)
```

### deterministic quick-fixes

The Tier 1 category map ([`validation-rules.md`](validation-rules.md)) supplies
deterministic fixes for R001/R002 (add a missing start or end event and connect it
to the inferred entry or exit) and R005/R006 (remove a dangling sequence flow).
R003/R004 and R007/R008 have no registered quick fix because the correct
rewiring or deletion cannot be determined from the local finding alone.

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

Given the trace, the model can identify the cause — here the classic exclusive-split / parallel-join mismatch — and emit a targeted fix such as `change_gateway_type` on `G`. Structured counterexample in, localised repair out. Whether that beats regenerating the whole diagram is what the `include_formal_evidence` ablation measures ([`experiments.md`](experiments.md)).

---

## convergence and iteration bound

- `ExperimentConfig.max_repair_iters` caps the outer loop. Default: **5**.
- Manual approval sets `single_plan=true`, so exactly one candidate plan and one
  revalidation run before the proposal is shown.
- Explicit auto-approval sets `single_plan=false` and may use the full iteration
  budget.
- The loop stops early when no repairable error or warning remains.
- Under `target_scoped_safe`, it instead stops when the frozen targets are gone;
  unrelated new warnings remain visible but do not become repair work.
- A hard regression restores the previous safe diagram and returns
  `stop_reason=regression_rejected`.
- `iterations` is written to `run.iterations` so experiments can report observed
  convergence behavior. Comparison with a theoretical iteration bound remains
  separate unless the implementation and theorem assumptions are shown to align.
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

For experiment evaluation, each repair run contributes:

- `iterations` — convergence speed.
- `len(applied_ops)` — minimality of the repair.
- `converged` — hit-rate against the test set.
- `ged(initial, final)` and `ged(final, ground_truth)` — how close the repair got to the expected fix.
- `wall_clock_ms` per iteration — latency budget check.

All of these are surfaced via `run` or the response body and are analysed from log data rather than by instrumenting the dispatcher further. See [`experiments.md`](experiments.md).
