# architecture

## overview

The system is a full-stack web application for **interactive validation, repair, and refinement of BPMN 2.0 diagrams using large language models**. A React frontend with bpmn-js gives the user a live modelling surface; a FastAPI backend runs three independent validator tiers behind a common repair loop. All LLM calls route through a provider-agnostic client. Every backend response carries a `run` block stamping exactly which model, prompts, rules, and converter produced it, so every experiment is reproducible.

The architecture is organised around four ideas:

- **Three validation tiers** — cheap deterministic rules first, formal model checking second, LLM semantic review third.
- **Repair as a toggleable loop** — atomic edit operations by default, full IR regeneration as a fallback; the LLM reasons about formal-checker counterexamples rather than a bare error message.
- **Multiple IRs as a first-class comparison surface** — one canonical IR drives internal logic; candidate IRs (YAML, Mermaid, compact-JSON) are swappable I/O formats used for comparison experiments.
- **Live + on-demand validation triggers** — tier 1 runs continuously on every edit; tier 2 and tier 3 run on explicit user action.

Items marked `(planned)` in this document are committed in the thesis architecture but not yet wired in code. See `TODO.md` at the workspace root for per-item status.

---

## operations

The system performs three distinct operations over a diagram:

| Operation | Driven by | Surface | Output |
|---|---|---|---|
| **validation** | diagram state | live (tier 1) + on-demand (tier 2/3) | issue list (read-only) |
| **repair** | specific issues from validation | explicit *Repair* action | proposed changes (`EditOp[]` or full IR) |
| **refinement** | user intent ("add a cancellation path") | chat | proposed changes (`EditOp[]` or full IR) |

Shared constraints:

- **Nothing applies automatically.** Every repair or refinement result is a **suggestion** until the user accepts it. Validation never modifies the diagram at all.
- **Repair and refinement share the `EditOp` schema.** They differ in what triggers them and what context they carry, not in what they emit.
- **Repair requires issues.** `/repair` is meaningless without an issue list — it operates over known problems. A frontend "Validate & Repair" button is a UI chain (`/validate` -> `/repair`), not a backend automation.
- **Refinement cannot be auto-triggered.** The system cannot infer intent; chat is always user-initiated.

Tier 3 findings that look like refinement suggestions (*"this task has a vague name"*) are still **issues**, not chat turns — the user can Repair them like any other issue. The rule of thumb: anything the system *finds* is an issue; anything the user *wants* goes through chat.

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
 ├── /repair             closed repair loop  (planned)
 └── /history/*          session-scoped revision log
       │
       ├── api/           routes, request/response schemas
       ├── services/      chat, diagrams, repair, validation
       ├── model/         IR layer (canonical + candidates)
       ├── validation/    tier 1 deterministic rules
       ├── llm/           provider-agnostic client, prompts, routing
       │                  tier 3 entry point; tier 2 integration (planned)
       └── history/       sessions and revisions
```

---

## request envelope — `ExperimentConfig`

Every request that touches validation or repair carries an `ExperimentConfig`. It bundles every parameter a user or experimenter can tune; the frontend exposes these as a configuration panel so the same backend serves both interactive sessions and ablation runs.

```
ExperimentConfig {
  model_tier:         "strong" | "fast" | "custom"
  model_override?:    str              // explicit model id when tier = custom
  ir_format:          "pydantic" | "yaml" | "mermaid" | "compact_json" | ...
  tiers_enabled:      { t1: bool, t2: bool, t3: bool }
  repair_mode:        "atomic" | "regen"
  max_repair_iters:   int
  temperature:        float
  seed?:              int
}
```

The config is hashed into `run.config_hash` so every result traces back to the exact parameter set that produced it. See [`experiments.md`](experiments.md) *(planned doc)*.

---

## response envelope — `run`

Every backend response includes a `run` block:

```
run {
  model_used:         str              // model the provider actually answered with
  prompt_versions:    {
    validate: { name: "validate_v1", hash: "ab34cd5e78f9" },
    repair:   { name: "repair_v1",   hash: "... 12 hex chars" },
    chat:     { name: "chat_system_v1", hash: "... 12 hex chars" }
  }
  converter:          str              // canonical IR version id
  rules_version:      str              // e.g. "R001-R011"
  config_hash:        str              // 12-char hex prefix of sha256(ExperimentConfig)
  timestamp:          iso-8601
  request_id:         uuid
  iterations?:        int              // repair loop only
}
```

All hashes are the **first 12 hex characters of sha256(file bytes)**. Full hashes are always reconstructible from the source file; 12 chars is git's long-form abbreviation — short enough to read inline, with a collision probability of ~10⁻¹⁰ for a thousand prompt versions. The `name` mirrors the file stem so logs stay human-readable.

Rationale: Phase 3 evaluation requires every metric to be replayable. `run` is cheap to emit and expensive to add retroactively — results collected without it cannot be trusted. See [`run.md`](run.md) *(planned doc)*.

---

## validation tiers

A diagram passes through three independent validators. Each emits a uniform `Issue` shape: `rule_id`, `severity`, `message`, `element_refs`, optional `counterexample`, optional `suggestion`.

### tier 1 — deterministic rules

Pure Python, zero external dependencies, runs in <50 ms on typical diagrams. Detects *local, structural* violations: missing start/end events, disconnected nodes, gateway branch counts, dangling references, duplicate IDs. Eleven rules today (R001–R011). Runs on the **live** trigger — on every edit. See [`validation-rules.md`](validation-rules.md).

### tier 2 — formal checker stack (planned)

Three open-source tools stacked under a uniform `CheckerIssue` schema so the repair layer does not care which tool produced a finding.

- **BPMN Analyzer 2.0** (Kräuter, Rust) — soundness, safeness, deadlock, livelock, lack of synchronisation; emits counterexample traces; sub-500 ms. Wrapped as a subprocess / sidecar.
- **PM4Py Woflan** (Python-native) — classical Petri-net soundness. Covers elements BPMN Analyzer 2.0 drops (data objects, non-message artefacts) via the BPMN -> Petri-net mapping PM4Py already provides.
- **BPMNspector** (uniba-dsg, Java) — BPMN 2.0 standards compliance across 611 constraints; complements behavioural checks with structural conformance.

Runs on the **on-demand** trigger (explicit *Deep Validate* action). See [`formal-checkers.md`](formal-checkers.md) *(planned doc)*.

### tier 3 — LLM semantic review

Single-turn LLM call over the IR plus tier 1 and tier 2 diagnostics. Targets concerns no deterministic or formal tool can express — label quality, role mislabelling, missing steps, process-intent issues. Runs on-demand, same surface as tier 2. See [`llm-integration.md`](llm-integration.md).

---

## repair loop (planned)

Repairs are always **suggestions by default**; the frontend only applies them on explicit user acceptance. The `/repair` endpoint orchestrates a closed loop:

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

When tier 2 produces a counterexample (e.g. a token-flow trace leading to a deadlock), the repair prompt includes it in structured form. The LLM reasons about *why* the diagram is broken, not just *that* it is. This is the neuro-symbolic hinge point of the system.

See [`repair-loop.md`](repair-loop.md) *(planned doc)*.

---

## refinement — chat

Conversational diagram evolution driven by **user intent**. Chat does not react to checker diagnostics; it reacts to the user's prompt.

- Endpoint: `/chat`
- Input: `{ messages, xml, ExperimentConfig }`
- Output: free-form reply plus an optional proposed change expressed per `repair_mode`:
  - `atomic` — a list of `EditOp`s
  - `regen` — a fenced replacement IR
- The change is previewed in bpmn-js; applied only on user acceptance.

Chat and repair share the `EditOp` schema; they differ only in what triggers them and what context accompanies the prompt. Chat can produce a repair-shaped response when the user asks for a fix — but what drives it is the conversation (history, stated goal), not a checker counterexample.

Chat is **not** auto-triggered and never applies changes silently.

---

## IR strategy

### canonical IR

One internal representation — the Pydantic-typed `BpmnDiagram` — is what every tier, the repair loop, and the history store operate on. The canonical IR aims for full BPMN 2.0 coverage; the authoritative coverage matrix lives in [`converter-format.md`](converter-format.md).

### candidate IRs

Additional formats are **I/O surfaces**, not replacement internal types: they parse into the canonical IR on ingest and serialize from it on output. This decouples IR experimentation from the validation code, which would otherwise fragment across schema types.

Planned candidate IRs:

- **Pydantic-JSON** — canonical, also usable as an I/O format (current default).
- **YAML** — novel thesis contribution; no prior BPMN-LLM YAML study exists.
- **Mermaid** — reference point for ~93% token reduction against raw BPMN XML.
- **compact-JSON** — minimal-key JSON for ablation.

Comparison runs (token reduction, generation quality, edit success) vary `ExperimentConfig.ir_format`.

### round-trip invariant

For every converter, `serialize(parse(xml))` must produce semantically equivalent BPMN. Candidate IRs additionally must round-trip through the canonical IR without loss for the supported subset. The invariant is a hard test gate.

---

## validation triggers

| Trigger       | Runs              | When                                            | Latency budget |
|---------------|-------------------|-------------------------------------------------|----------------|
| **live**      | tier 1            | every diagram edit, debounced ~500 ms           | <50 ms         |
| **on-demand** | tier 2 + tier 3   | explicit *Deep Validate* action                 | <5 s           |
| **repair**    | full loop         | explicit *Repair* action                        | bounded by `max_repair_iters` |

Live validation is read-only: it produces issues, never modifies the diagram. The *no silent mutations* principle applies to **applying** repairs, not to **running** checks.

---

## backend layers

### model layer — `backend/app/model/`

Owns the canonical IR and the converter registry.

- `schema.py` — canonical `BpmnDiagram`, `BpmnProcess`, `FlowNode`, `SequenceFlow`.
- `protocol.py` — `DiagramConverter` protocol.
- `registry.py` — short-name -> converter instance; default chosen via `DIAGRAM_CONVERTER` env var.
- `formats/` — one module per converter (`pydantic_ir.py` today; YAML, Mermaid, compact-JSON planned).

### validation layer — `backend/app/validation/`

Houses tier 1 deterministic rules. When tier 2 is wired, this layer will also hold the adapters that normalise each tool's output into the shared `Issue` schema. Zero LLM dependency.

### llm layer — `backend/app/llm/`

- `client.py` — high-level entry points (`complete`, `complete_with_history`). Every LLM call routes through here.
- `protocol.py` + `providers/` — provider abstraction. Current implementations: Anthropic, OpenAI, Ollama. Only `providers/` modules touch vendor SDKs.
- `registry.py` — provider registration at startup.
- `router.py` — `TaskType` -> model tier -> concrete provider + model id.
- `prompts/` — versioned `.txt` files (`validate.txt`, `repair.txt`, `chat_system.txt`). Hashed at load for `run.prompt_versions`.

### history layer — `backend/app/history/`

Session-scoped revision log. Every diagram change (upload, repair acceptance, chat-accepted suggestion) creates a revision. `/history/{session_id}` exposes list, get, and revert.

### services layer — `backend/app/services/`

Business logic decoupled from HTTP: `chat.py`, `diagrams.py`, `repair.py` (service scaffolding exists; route not yet wired), `validation.py`.

### api layer — `backend/app/api/routes/`

Thin adapters over service-layer functions. Current routes: `diagrams`, `validate`, `chat`, `history`. Planned: `repair`.

---

## data flows

### live edit -> tier 1

```
user edit in bpmn-js
  -> debounce ~500 ms
  -> POST /validate { xml, ExperimentConfig, tiers: [1] }
  -> canonical converter: parse
  -> rules.validate(diagram)
  -> response { issues, run }
  -> bpmn-js marker overlay + ValidationPanel update
```

### deep validate -> tier 1 + 2 + 3

```
user clicks "Deep validate"
  -> POST /validate { xml, ExperimentConfig, tiers: [1, 2, 3] }
  -> parse -> tier 1 rules
  -> fan out to tier 2 checkers in parallel, normalise to Issue
  -> tier 3 LLM call with full IR + tier 1/2 diagnostics
  -> merge into a unified Issue list
  -> response { issues, run }
```

### repair -> iterative loop (planned)

```
user clicks "Repair" on one or more issues
  -> POST /repair { xml, issues, ExperimentConfig }
  -> dispatcher loop:
      1. pick an issue
      2. deterministic quick-fix?  -> apply
         else -> LLM repair call with counterexample context
                (mode=atomic emits EditOp[]; mode=regen emits full IR)
      3. apply to canonical IR
      4. revalidate via tier 1 (and tier 2 if configured)
      5. continue until converged or iterations > max
  -> response { updated_xml, iterations, applied_ops, remaining_issues, run }
  -> frontend previews diff; user accepts or rejects
```

### conversational refinement

```
user types in chat
  -> POST /chat { messages, xml, ExperimentConfig }
  -> LLM emits an EditOp plan or a fenced diagram, per repair_mode
  -> frontend renders as a proposed change
  -> change is applied only on explicit user acceptance
```

---

## design principles

- **Deterministic before probabilistic.** Tier 1 runs before tier 2; tier 2 before tier 3. Cheap, testable checks narrow what the LLM is asked to reason about.
- **No silent diagram mutations.** Validation never modifies a diagram. Repairs are always suggestions until the user accepts them.
- **Swappable everywhere that matters.** Provider, prompt file, IR format, and tier-2 tool set are all swappable via `ExperimentConfig` or a registry. No hard-coded model or format choices outside configuration.
- **Every response is reproducible.** The `run` block on every response is a hard contract — no endpoint omits it.
- **Round-trip fidelity is non-negotiable.** Every converter must satisfy `serialize(parse(xml))` semantically equivalent to the input.

---

## doc map

| Topic | Doc |
|---|---|
| IR protocol, canonical IR, candidate IRs, BPMN 2.0 coverage matrix | [`converter-format.md`](converter-format.md) |
| Tier 1 deterministic rules | [`validation-rules.md`](validation-rules.md) |
| Tier 2 formal checker stack | [`formal-checkers.md`](formal-checkers.md) *(planned)* |
| LLM client, routing, prompts, structured outputs, counterexample prompting | [`llm-integration.md`](llm-integration.md) |
| Repair modes, dispatcher, edit ops | [`repair-loop.md`](repair-loop.md) *(planned)* |
| `ExperimentConfig` schema, metrics, reproducibility, datasets | [`experiments.md`](experiments.md) *(planned)* |
| `run` metadata contract | [`run.md`](run.md) *(planned)* |
