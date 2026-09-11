# architecture

## overview

A web application for validating, repairing, and refining BPMN 2.0 diagrams with large language models. A React frontend with bpmn-js gives the user a live modelling surface; a FastAPI backend runs three independent validator tiers behind a common repair loop. All LLM calls route through a provider-agnostic client. Validation, repair and chat responses record execution metadata in a `run` block once a run context exists; see [`run.md`](run.md) for error-response coverage.

Four ideas shape the architecture:

- Three validation tiers: cheap deterministic rules first, formal model checking second, LLM semantic review third.
- Repair runs as a toggleable loop. Atomic edit operations by default, full IR regeneration as a fallback; the LLM is given formal-checker counterexamples rather than a bare error message.
- One canonical IR drives internal logic. Candidate IRs (YAML, Mermaid, compact-JSON) are swappable I/O formats used for comparison experiments.
- Validation is user-triggered. Separate actions run deterministic rules, rules plus
  Woflan, or rules plus LLM semantic review.

---

## operations

The system performs three distinct operations over a diagram:

| Operation | Driven by | Surface | Output |
|---|---|---|---|
| **validation** | diagram state | explicit rule, formal, or semantic action | issue list (read-only) |
| **repair** | specific issues from validation | explicit *Repair* action | proposed changes (`EditOp[]` or full IR) |
| **refinement** | user intent ("add a cancellation path") | chat | proposed changes (`EditOp[]` or full IR) |

Shared constraints:

- **Manual approval by default.** Repair and refinement results remain proposals until accepted. Explicit Auto mode permits application without a separate acceptance step. Validation never applies changes.
- **Repair requires issues.** `/repair` operates over known problems, so it does nothing without an issue list. The frontend exposes separate validation and Repair actions.
- **Refinement cannot be auto-triggered.** The system cannot infer intent; chat is always user-initiated.

Repair and refinement emit the same `EditOp` schema. They differ in what triggers them and what context they carry.

Tier 3 findings that read like refinement suggestions (*"this task has a vague name"*) are still issues, not chat turns — the user can repair them like any other. Anything the system *finds* is an issue; anything the user *wants* goes through chat.

---

## system topology

```
browser
 └── React + bpmn-js (dev :5173 / nginx :80)
       │
       │  HTTP /api/v1/*
       ▼
FastAPI (port 8000)
 ├── /diagrams/*         parse, serialize, export
 ├── /validate           run the validation pipeline (tiers 1–3)
 ├── /chat               conversational refinement
 ├── /repair             closed repair loop (dispatcher)
 ├── /repair/xml         repair BPMN XML that does not parse into the IR
 ├── /repair/apply       apply a user-selected subset of edit ops
 └── /history/*          session-scoped revision log
       │
       ├── api/           routes, request/response schemas
       ├── services/      chat, diagrams, repair, validation
       ├── model/         IR layer (canonical + candidates)
       ├── validation/    tier 1 deterministic rules
       ├── llm/           provider-agnostic client, prompts, routing
       │                  tier 3 entry point; tier 2 = Woflan
       └── history/       sessions and revisions
```

---

## request envelope — `ExperimentConfig`

Every request that touches validation, repair, or chat resolves an
`ExperimentConfig`. It bundles every parameter an experimenter can tune. The
frontend exposes provider, model, and reasoning controls plus fixed
validation-mode actions; the CLI and experiment specs expose the complete
ablation surface.

```
ExperimentConfig {
  model_tier:         "strong" | "fast" | "custom"
  model_override?:    str              // explicit model id when tier = custom
  provider_override?: str
  ir_format:          "pydantic" | "yaml" | "mermaid" | "compact_json" | ...
  tiers_enabled:      { t1: bool, t2: bool, t3: bool }
  include_formal_evidence: bool
  include_reference_description: bool
  include_semantic_projection: bool
  llm_validation_scope: "semantic" | "holistic"
  repair_mode:        "atomic" | "regen"
  repair_loop_policy?: "legacy_all_findings" | "target_scoped_safe"
  max_repair_iters:   int
  temperature?:       float
  reasoning_effort?:  "none" | "low" | "medium" | "high" | "xhigh"
  seed?:              int
  experiment_id?:     str
  notes?:             str
}
```

The config is hashed into `run.config_hash` so every result traces back to the exact parameter set that produced it. See [`experiments.md`](experiments.md).

---

## response envelope — `run`

Every validation, repair, and chat response includes a `run` block:

```
run {
  model_used:         str              // model(s) actually called, "none" if no call was made
  model_configured?:  str              // what the config resolved to, called or not
  prompt_versions:    {
    validate: { name: "validate_v1", hash: "ab34cd5e78f9" },
    repair:   { name: "repair_v1",   hash: "... 12 hex chars" },
    chat:     { name: "chat_system_v1", hash: "... 12 hex chars" }
  }
  converter:          str              // canonical IR version id
  rules_version:      str              // e.g. "R001-R008"
  config_hash:        str              // 12-char hex prefix of sha256(ExperimentConfig)
  timestamp:          iso-8601
  request_id:         uuid
  iterations?:        int              // repair loop only
}
```

All hashes are the **first 12 hex characters of sha256(file bytes)**. Full hashes are always reconstructible from the source file; 12 chars is git's long-form abbreviation — short enough to read inline, with a collision probability of ~10⁻¹⁰ for a thousand prompt versions. The `name` mirrors the file stem so logs stay human-readable.

The `run` envelope makes every metric traceable to the configuration that produced it. See [`run.md`](run.md).

---

## validation tiers

A diagram passes through three independent validators. Each emits a uniform `Issue` shape: `rule_id`, `severity`, `message`, `element_refs`, optional `counterexample`, optional `suggestion`.

### tier 1 — deterministic rules

Pure Python, zero external dependencies, runs quickly on typical diagrams. Detects *local, structural* violations: missing start/end events, dangling references; and unreachable nodes or traps (a linear-time under-approximation of soundness). Every rule is an `error` — heuristic "might be a problem" checks are deferred to tier 2 / tier 3 rather than emitted as deterministic warnings. Eight rules today (R001–R008). Runs when the user selects any validation action. See [`validation-rules.md`](validation-rules.md).

### tier 2 — formal validation

**PM4Py Woflan** performs Petri-net soundness analysis through PM4Py's BPMN to
Petri-net mapping. It runs synchronously in-process, localizes diagnostic names
to stable BPMN element IDs, and exposes dead elements, uncovered elements, and
locking-scenario traces through the shared formal-witness schema.

Runs on the explicit **Formal Validate** action. See [`formal-checkers.md`](formal-checkers.md).

### tier 3 — LLM semantic review

Single-turn LLM call over the IR plus tier 1 diagnostics. Targets concerns no deterministic or formal tool can express — label quality, role mislabelling, missing steps, process-intent issues. Runs through the explicit **Semantic LLM** action. See [`llm-integration.md`](llm-integration.md).

---

## repair loop

Repairs are proposals by default. Manual mode requests one plan (`single_plan=true`) for review. Explicit Auto mode requests the closed loop (`single_plan=false`) and applies its result without separate acceptance. The dispatcher follows this flow:

```
issues (tier 1 + 2 + 3)
  │
  ▼
repair dispatcher
  │
  ├── deterministic quick-fix available?   -> apply, revalidate
  │
  └── else -> LLM repair call
               prompt = {IR, issue, counterexample?, config}
               mode=atomic  -> EditOp[]
               mode=regen   -> full IR
                                           │
                                           ▼
                                 apply to canonical IR, revalidate
  │
  ▼
converged? stop.   iterations > max? stop.   else loop.
```

### repair modes

- **atomic** — LLM emits a list of `EditOp`s (`add_flow`, `remove_node`, `change_gateway_type`, `rename_element`, ...). Server applies them to the canonical IR. Default — lower token cost, higher edit success rate, localised changes.
- **regen** — LLM emits a full replacement IR. Fallback for large-scale repairs and as a baseline in comparison experiments.

Mode is selected per request via `ExperimentConfig.repair_mode`.

### counterexample context

When tier 2 produces a witness, the repair prompt includes it in a structured `tier2_findings` section (`_extract_tier2_findings`), so the LLM is told *why* the diagram is broken and not only *that* it is. This is where the checker output meets the model, and the comparison the thesis is built on.

See [`repair-loop.md`](repair-loop.md).

---

## refinement — chat

Conversational diagram evolution driven by **user intent**. Chat does not react to checker diagnostics; it reacts to the user's prompt.

- Endpoint: `/chat`
- Input: `{ messages, xml, ExperimentConfig }`
- Provider output: a schema-constrained envelope whose `description` becomes
  the conversational reply and whose `result.diagram` is either a serialized
  complete IR or `null`.
- The change is previewed in bpmn-js; applied only on user acceptance.

Chat and repair share the common outer response convention but use different
task-specific result schemas. What drives chat is the conversation (history,
stated goal), not a checker counterexample.

Chat is user-initiated. Manual mode presents changes for review; explicitly selected Auto mode applies them without a separate acceptance step.

---

## IR strategy

### canonical IR

One internal representation — the Pydantic-typed `BpmnDiagram` — is what every tier, the repair loop, and the history store operate on. The canonical IR aims for full BPMN 2.0 coverage; the authoritative coverage matrix lives in [`converter-format.md`](converter-format.md).

### candidate IRs

Additional formats are I/O surfaces, not replacement internal types: they parse into the canonical IR on ingest and serialize from it on output. Without that, IR experimentation would fragment the validation code across schema types.

Candidate IRs (all wired, selected via `ExperimentConfig.ir_format`):

- **Pydantic-JSON** — canonical, also usable as an I/O format.
- **YAML** — the novel candidate; no prior BPMN-LLM YAML study exists.
- **Mermaid** — the strong baseline for token reduction against raw BPMN XML.
- **compact-JSON** — minimal-key JSON, for ablation.

Comparison runs (token reduction, generation quality, edit success) vary `ExperimentConfig.ir_format`.

### diagram-level invariants

Document-scoped identifier uniqueness is enforced by a validator on the canonical `BpmnDiagram` itself, not in any one converter. Every input path — all five IR formats, LLM-authored payloads, edit-op results — constructs a `BpmnDiagram`, so one check covers them all. It buys more than spec compliance: the tier-1 reachability rules key their adjacency index by node id, so a duplicate would silently shadow its twin and misdirect the analysis.

### round-trip invariant

For every converter, `serialize(parse(xml))` must produce semantically equivalent BPMN. Candidate IRs additionally must round-trip through the canonical IR without loss for the supported subset. The invariant is a hard test gate.

---

## validation triggers

| Trigger | Runs | When | Latency budget |
|---|---|---|---|
| **Verify Rules** | tier 1 | explicit structural-validation action | <50 ms |
| **Formal Validate** | tier 1 + tier 2 | explicit formal-validation action | <5 s |
| **Semantic LLM** | tier 1 + tier 3 | explicit semantic-validation action | model-dependent |
| **repair** | configured revalidation loop | explicit *Repair* action | bounded by `max_repair_iters` |

Validation is read-only: it produces issues and never modifies the diagram. The
*no silent mutations* principle applies to **applying** repairs, not to **running** checks.

---

## backend layers

### model layer — `backend/app/model/`

Owns the canonical IR and the converter registry.

- `schema.py` — canonical `BpmnDiagram`, `BpmnProcess`, `FlowNode`, `SequenceFlow`.
- `protocol.py` — `DiagramConverter` protocol.
- `registry.py` — short-name -> converter instance; default chosen via `DIAGRAM_CONVERTER` env var.
- `formats/` — one module per converter: `pydantic_ir.py` (canonical), `pydantic_json.py`, `yaml_ir.py`, `mermaid.py`, `compact_json.py`.

### validation layer — `backend/app/validation/`

Houses tier 1 deterministic rules and the Woflan adapter that normalises formal-checker output into the shared `Issue` schema. Zero LLM dependency.

### llm layer — `backend/app/llm/`

- `client.py` — high-level entry points (`complete`, `complete_with_history`). Every LLM call routes through here.
- `protocol.py` + `providers/` — provider abstraction. Current implementations: Anthropic, OpenAI, Gemini, Ollama, Codex CLI and Claude Code. Hosted adapters require credentials; CLI adapters require an available executable; Ollama is always registered. Only `providers/` modules touch vendor SDKs.
- `registry.py` — provider registration at startup.
- `router.py` — `TaskType` -> model tier -> concrete provider + model id.
- `prompts/` — versioned `.txt` files (`validate.txt`, `repair.txt`, `chat_system.txt`). Hashed at load for `run.prompt_versions`.

### history layer — `backend/app/history/`

Session-scoped revision log. Uploads and accepted or auto-approved changes create revisions. Uncommitted canvas edits do not create a revision for each edit. `/history/{session_id}` exposes list, get, and revert.

### services layer — `backend/app/services/`

Business logic decoupled from HTTP: `chat.py`, `diagrams.py`, `repair.py`, `validation.py`, `ir_payload.py`.

### api layer — `backend/app/api/routes/`

Thin adapters over service-layer functions. Current routes: `diagrams`, `validate`, `chat`, `repair` (plus `/repair/xml`, `/repair/apply`), `history`.

---

## data flows

### explicit rules validation -> tier 1

```
user clicks "Verify Rules"
  -> POST /validate { xml, ExperimentConfig, tiers: [1] }
  -> canonical converter: parse
  -> rules.validate(diagram)
  -> response { issues, run }
  -> ValidationPanel update
```

### formal or semantic validation

```
user clicks "Formal Validate" or "Semantic LLM"
  -> POST /validate with tiers [1, 2] or [1, 3]
  -> parse -> tier 1 rules
  -> selected tier 2 Woflan check or tier 3 LLM review
  -> merge into a unified Issue list
  -> response { issues, run }
```

### repair -> iterative loop

```
user clicks "Repair" with current validation findings
  -> POST /repair { xml, issues, config, single_plan }
  -> dispatcher loop:
      1. choose the current target findings
      2. deterministic quick-fix?  -> apply
         else -> LLM repair call with counterexample context
                (mode=atomic emits EditOp[]; mode=regen emits full IR)
      3. apply to canonical IR
      4. revalidate with the configured tiers
      5. return one plan for review, or continue within the closed-loop budget
  -> response { updated_xml, iterations, applied_ops, remaining_issues, run }
  -> manual mode: preview diff for acceptance or rejection
  -> explicit Auto mode: apply the result
```

### conversational refinement

```
user types in chat
  -> POST /chat { messages, diagram, config, snapshot_changes }
  -> LLM emits a description plus an optional serialized complete diagram
  -> manual mode: frontend presents a proposal for acceptance
  -> explicit Auto mode: frontend applies the returned diagram
```

---

## design principles

- **Deterministic before probabilistic.** Tier 1 runs before tier 2, tier 2 before tier 3. Cheap, testable checks narrow what the LLM is asked to reason about.
- **No silent diagram mutations.** Validation never modifies a diagram. Repairs require acceptance in manual mode; automatic application requires explicitly selected Auto mode.
- **Configuration, not code, picks the variant.** Provider, prompt file, IR format, and tier-2 tool set are all selected through `ExperimentConfig` or a registry. Nothing outside configuration hard-codes a model or a format.
- **Every response is reproducible.** Validation, repair and chat responses carry a `run` block once a run context exists; request parsing failures precede that context. See [`run.md`](run.md).
- **Round-trip fidelity holds.** Every converter must satisfy `serialize(parse(xml))` semantically equivalent to the input, enforced as a test gate.

---

## doc map

| Topic | Doc |
|---|---|
| IR protocol, canonical IR, candidate IRs, BPMN 2.0 coverage matrix | [`converter-format.md`](converter-format.md) |
| Tier 1 deterministic rules | [`validation-rules.md`](validation-rules.md) |
| Tier 2 Woflan checker | [`formal-checkers.md`](formal-checkers.md) |
| LLM client, routing, prompts, structured outputs, counterexample prompting | [`llm-integration.md`](llm-integration.md) |
| Repair modes, dispatcher, edit ops | [`repair-loop.md`](repair-loop.md) |
| `ExperimentConfig` schema, metrics, reproducibility, datasets | [`experiments.md`](experiments.md) |
| `run` metadata contract | [`run.md`](run.md) |
