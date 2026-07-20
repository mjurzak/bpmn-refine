# formal checkers — tier 2

Tier 2 is the formal-verification layer. It runs **on demand** (explicit formal-validate action), not on every edit, because it is heavier than tier 1 and its findings are the input that makes LLM-guided repair non-trivial.

**Status (2026-07-20):** one checker is wired — **PM4Py Woflan**, in-process. The BPMN Analyzer 2.0 and BPMNspector adapters described below, and the parallel fan-out across them, are planned. The wired Woflan slice returns a soundness verdict, diagnostic messages, and dead transitions, but no firing trace and no BPMN-element-level mapping yet (see the Woflan subsection). Sections that describe the full three-tool stack are the target design, not the current state.

Tier 1 catches local, structural mistakes. Tier 2 catches behavioural problems that require exploring the diagram's **state space**: deadlocks, livelocks, unreachable termination, token-count mismatches, safeness and standards-conformance violations.

This doc covers **why** the system uses a stack of open-source tools rather than one, which tools are in the stack, what each contributes, the unified issue shape they produce, and how the repair loop consumes their counterexamples.

---

## why a stack, not a single tool

No single open-source BPMN formal checker covers the full BPMN 2.0 specification with usable counterexamples. Every tool trades something:

- **BPMN Analyzer 2.0** — excellent counterexamples and sub-500 ms latency, but ignores data/artifacts and treats each pool as one process.
- **PM4Py Woflan** — classical Petri-net soundness that can in principle cover anything expressible as a Petri net, but the richness of its report depends on the BPMN -> PN mapping.
- **BPMNspector** — 611 BPMN 2.0 standards constraints, but structural/conformance only — no behavioural verification.

Stacking them under a **uniform** issue shape gives the repair layer one thing to consume and the user one panel to read.

And: formal BPMN verification is a 20-year-old research area. The thesis contribution is the **neuro-symbolic integration** — how checker output is used to drive LLM repair — not a from-scratch verifier. Wrapping the best open-source tools is the cost-effective path to that contribution.

---

## the stack

### BPMN Analyzer 2.0 (planned — intended primary)

- **Origin:** Tim Kräuter et al., BPM 2024 (open source, Rust).
- **Checks:** option-to-complete (deadlock-freedom), proper completion, safeness, lack of synchronisation, dead activities.
- **Counterexamples:** token-flow traces ending in the violation, including the marking at the point of failure.
- **Deterministic quick-fixes:** bundled for a subset of violations (mismatched gateway types, untriggered message events, unsafe sequence flows). These feed the [repair dispatcher](repair-loop.md) directly.
- **Coverage:** "green" Camunda 8 elements — all major events, all five gateways, sub-processes, call activities. Limitations: markers, data objects, and non-message artifacts are ignored; multiple pools run as one process each.
- **Latency:** <500 ms on typical diagrams.
- **Wrapping:** shipped binary invoked as a subprocess (or as a sidecar HTTP service in Docker Compose). Input is the canonical IR serialised to BPMN XML via `PydanticConverter`. Output is JSON containing issues + counterexamples.

### PM4Py Woflan (wired)

- **Origin:** PM4Py's built-in soundness analysis, implementing Woflan's classical Petri-net soundness.
- **Checks:** soundness (option-to-complete, proper completion, no dead transitions), bounded-ness for P/T nets.
- **Wrapping:** Python-native — no subprocess, no sidecar. Runs in-process on a worker thread under a configured timeout (`checkers.yaml`); a crash or timeout degrades to a warning rather than failing the request.
- **What the adapter emits today:** a boolean soundness verdict, Woflan's diagnostic messages, and dead transitions as `element_refs`, normalised into the shared `ValidationIssue` shape with `rule_id = "woflan:soundness"`.
- **What it does *not* emit yet (adapter-side gaps, not tool limits):** Woflan returns a minimal coverability graph (`mcg`), `not_well_handled_pairs`, and locking scenarios that the adapter currently discards. So there is no firing trace, no marking-at-failure, and no separately surfaced safeness finding. `FormalWitness.kind` is hard-coded to `"soundness"` — the `deadlock`/`livelock` values the repair prompts advertise are never produced.
- **Known bug:** the diagnostic text and `element_refs` name Petri-net *places* and can include Woflan's synthetic `short_circuited_transition`, which is not an element of the user's diagram. Mapping these back to BPMN element ids is the first fix (see `TODO.md`).

### BPMNspector (standards compliance)

- **Origin:** uniba-dsg; LGPL v3; Java.
- **Checks:** 611 BPMN 2.0 standard constraints across four categories — CARD (cardinality), VAL (value), REF (reference), EXT (other). Includes XSD schema validation.
- **Counterexamples:** no behavioural traces — findings identify the offending element and the constraint violated. Complements (does not replace) the behavioural tools.
- **Wrapping:** JAR invoked as a subprocess; output parsed into the unified `CheckerIssue` shape.
- **Role:** catches BPMN 2.0 specification violations that slip past tier 1's curated rule set. Especially useful when the canonical IR has been reconstructed from a candidate IR (YAML, Mermaid) and might emit subtly non-standard XML.

### tier 0 — bpmnlint (optional, browser-side)

Not part of the backend stack, but worth naming: `bpmnlint` (bpmn-io) runs **in the browser**, inside bpmn-js, and can surface live-lint markers with zero backend round-trip. Useful for UX polish — highlighting missing labels or obviously missing events before the user even triggers live tier 1 validation.

If adopted, it sits *before* tier 1, not competing with it: bpmnlint catches stylistic issues that never reach the backend, while tier 1+ operates on canonicalised IR the backend owns.

---

## unified `CheckerIssue` schema

Every tier-2 tool is normalised into this shape before reaching the repair layer:

```
CheckerIssue {
  rule_id:          str              // e.g. "analyzer:option-to-complete",
                                     //      "woflan:soundness:dead-transition",
                                     //      "bpmnspector:REF:41"
  severity:         "error" | "warning" | "info"
  message:          str              // human-readable summary
  element_refs:     [str]            // canonical IR node/flow IDs
  source:           "bpmn_analyzer" | "woflan" | "bpmnspector"
  counterexample?:  Counterexample   // present when the tool emitted one
  raw?:             dict             // the tool's original output, preserved for traceability
}

Counterexample {
  kind:             "deadlock" | "livelock" | "unreachable" | "dead_activity" | ...
  trace:            [TraceStep]
  marking?:         { node_id: token_count, ... }  // at the point of failure
  description:      str                             // short natural-language summary
}

TraceStep {
  step:             int
  fired:            str              // node or edge id that "fired" at this step
  marking_after:    { node_id: token_count, ... }
}
```

The schema is what the repair dispatcher and the repair prompt consume — see [`repair-loop.md`](repair-loop.md).

---

## routing — which tool runs on which input

The default orchestration when tier 2 is enabled:

1. **BPMNspector** runs first, over the canonical IR serialised back to BPMN XML. Cheap, parallel-safe, catches standards violations that make later checks nonsensical.
2. **BPMN Analyzer 2.0** runs next, on the same XML. Produces the richest behavioural diagnostics when it can analyse the diagram.
3. **PM4Py Woflan** runs when BPMN Analyzer 2.0 explicitly declines (elements it ignores are present and load-bearing) or as a second opinion configurable via `ExperimentConfig`.

All three run in parallel where possible. Results are merged into a single `Issue[]`; duplicates (same canonical root cause from two tools) are de-duplicated on `(element_refs, kind)`.

---

## configuration

Tier 2 behaviour is controlled per-request via `ExperimentConfig`:

```
ExperimentConfig.tiers_enabled.t2 : bool     // on/off (default: on for /validate, off for /chat)
```

Per-tool toggles are a config-file concern, not per-request:

```
# backend/app/validation/checkers.yaml  (planned)
bpmn_analyzer:
  enabled: true
  binary_path: /opt/bpmn-analyzer/bpmn-analyzer
  timeout_ms: 2000
woflan:
  enabled: true
  include_as_fallback_only: true
bpmnspector:
  enabled: true
  jar_path: /opt/bpmnspector/bpmnspector.jar
  categories: [CARD, VAL, REF, EXT]
```

Disabling a tool is how ablation experiments ("what does the system miss without BPMN Analyzer 2.0?") are run cleanly.

---

## coverage — what tier 2 can and cannot catch

Combining the stack covers the union of:

- behavioural soundness (BPMN Analyzer 2.0 + Woflan)
- safeness and boundedness (BPMN Analyzer 2.0)
- BPMN 2.0 schema and constraint compliance (BPMNspector)
- data-flow soundness where the mapping supports it (Woflan)

What tier 2 **does not** catch:

- labelling quality and process-intent issues — tier 3's job.
- resource allocation / organisational semantics — out of scope for this system.
- requirements traceability — out of scope.

Tier 2 also inherits the canonical IR's coverage gaps ([`converter-format.md`](converter-format.md)): behavioural analysis over elements the IR does not model cannot happen until the IR is extended.

---

## deployment

All three tools are containerised alongside the backend in `docker-compose.yml`:

- `bpmn-analyzer` — Rust binary, exposed as an HTTP sidecar on an internal port.
- `bpmnspector` — JVM, invoked as a subprocess from the backend (process lifetime is short; pooling optional).
- `woflan` — Python, runs in-process.

The `/validate` route fans out to the enabled tools and normalises their output into `Issue[]` before returning. Total latency target under default configuration: **under 5 s for diagrams of typical thesis-size**.

---

## integration hooks — where tier 2 plugs in

- `backend/app/validation/` — adapters per tool, each exposing `run(diagram: BpmnDiagram) -> list[ValidationIssue]` with tier-2 metadata.
- `backend/app/services/validation.py` — orchestrates tier 1 + tier 2 + tier 3 merge. The first implemented tier-2 slice runs Woflan; Analyzer and BPMNspector adapters remain planned.
- `backend/app/api/routes/validate.py` — the HTTP surface; accepts `ExperimentConfig.tiers_enabled`, returns the merged `Issue[]` plus `run`.

See `TODO.md` Phase 2f for the remaining Analyzer, BPMNspector, and full fan-out work.
