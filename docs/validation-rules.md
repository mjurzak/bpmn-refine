# validation rules — tier 1

Tier 1 is the deterministic rule layer. All rules live in `backend/app/validation/rules.py`; they run synchronously, require no external dependencies, and have no LLM dependency.

Tier 1 is one of three validation tiers (see [`architecture.md`](architecture.md) for the full picture):

| Tier | Runs | Catches |
|---|---|---|
| **1 — deterministic rules** | on explicit validation actions; also during configured repair revalidation | local structural violations |
| 2 — formal checker stack | on demand | Woflan workflow-net soundness and available locking traces ([`formal-checkers.md`](formal-checkers.md)) |
| 3 — LLM semantic review | on demand | label quality, role mislabelling, process-intent issues ([`llm-integration.md`](llm-integration.md)) |

## rationale for Tier 1

A natural objection: a soundness checker (workflow-net / Petri-net, à la van der Aalst, Corradini, Kräuter) can decide far more than these rules — so why keep a rule layer at all?

Because the formal checker is **not** strictly more powerful. It checks **one** family of properties (option to complete, proper completion, no dead transitions) over **one** abstraction: the control-flow graph with ids, references, labels, conditions and data thrown away. That abstraction is bracketed on both sides:

- **below it** — the checker *presupposes* a well-formed object. It cannot build a net from a diagram with a dangling reference, malformed import, or no source/sink. It chokes; it does not "detect". Duplicate ids are rejected by the BPMN parser before tier 1 because the canonical IR cannot represent them safely.
- **beside it** — it abstracts conditions to nondeterministic choice, so an undecidable branch is "sound" to it while being broken at runtime.

So every tier-1 rule must fall into exactly one justification **class**. If it falls into none, it is cut.

| Class | Justification | Relationship to tier 2 |
|---|---|---|
| **A — integrity / translation precondition** | the formal checker cannot run until these hold | *enabling*, not redundant |
| **B — graph under-approximation of soundness** | linear-time, element-local; **firing implies the model is necessarily unsound** | a sound but incomplete, fast, localised proxy run when tier 1 is enabled |

The keep/cut criterion for class B is the sharp one: **does firing the rule imply a real defect?** Equivalently, is the rule a sound under-approximation of some real property? "No start event" => no token source => unsound. "Multiple start events" => *still sound* => not a defect => **cut**. A join gateway is *sound* => a "fewer than 2 outgoing" rule would be wrong => **cut**.

## severity

| Level | Meaning | Used in tier 1 |
|---|---|---|
| `error` | Diagram is structurally invalid; must be fixed before repair / export | **yes — all rules** |
| `info` | Informational; no action required | no |

The `Severity` enum still defines `warning`/`info` so tier 2 and tier 3 can use them in the shared `Issue` shape.

---

## rules

### class A — integrity / translation preconditions

| ID | Severity | Condition | Rationale |
|---|---|---|---|
| R001 | error | process has no start event | no source place; the net cannot be built and the process is unrunnable |
| R002 | error | process has no end event | no sink place; the process can never *complete* in the Petri-net sense |
| R003 | error | start event has no outgoing sequence flow | a start with nowhere to go is effectively missing |
| R004 | error | end event has no incoming sequence flow | an unreachable terminator; the process can never end through it |
| R005 | error | sequence flow references an unknown source id | dangling reference; the flow cannot be rendered, executed, or translated |
| R006 | error | sequence flow references an unknown target id | same as R005 |

### class B — graph under-approximations of soundness

Each of these is a linear-time, exact graph check whose firing *guarantees* the model is unsound, so tier 2 would also fail — tier 1 just finds it with element-level localization, with no state-space walk.

| ID | Severity | Condition | Rationale |
|---|---|---|---|
| R007 | error | connected element not reachable from any start event | a dead node that can never be activated => unsound |
| R008 | error | connected element that cannot reach any end event | a trap; the process can never properly complete through it => unsound |

Reachability is suppressed in a direction whose anchor is missing (no start => no R007, no end => no R008) so these do not just echo R001/R002 across every node. Fully *disconnected* elements (no incoming and no outgoing) are skipped: in isolation they are an in-progress modelling artefact, not a defect a deterministic rule should hard-fail on, so they are left to tier 2 / tier 3.

### not checked: multiple start events, gateway heuristics, disconnected fragments

Several checks were considered and **cut** because they fail the class criteria:

- **multiple start events** — BPMN 2.0 permits them and a sound model can have many; firing never implies a defect.
- **gateway split/join mismatch, no-op / ambiguous gateways, implicit splits** — heuristic under-approximations that can be fooled by re-converging branches, conditions the net abstracts away, or legitimate modelling style. As deterministic rules they produced warnings, not errors; they belong to tier 2 (which confirms a real soundness violation) and tier 3 (which reasons about intent).
- **disconnected fragments** — a stylistic / in-progress signal, not a structural error.

---

## worked examples

`data/rule_cases/` holds one minimal fixture per rule, each isolating its target so the mapping rule->example is checkable. `R000_valid_baseline.bpmn` is well formed and fires nothing (it also exercises multi-start and a matched gateway pair to prove the rules do not over-fire). `backend/tests/test_rule_cases.py` validates the whole folder against the expected-issue table. Parser/import preconditions live under `data/import_cases/`. Document-scoped identifier uniqueness is enforced by a validator on `BpmnDiagram`, so it holds across all five IR formats and the LLM payload paths, not just XML upload.

---

## categories and the repair dispatcher

The dispatcher chooses a repair implementation separately from the user's approval mode. See [`repair-loop.md`](repair-loop.md).

| Implementation | Applies to | Behaviour |
|---|---|---|
| Deterministic quick fix | R001/R002 | add the missing start/end event and connect it to a heuristically selected entry/exit when available |
| Deterministic quick fix | R005/R006 | remove the dangling sequence flow |
| LLM plan or regeneration | findings without an applicable quick fix, including R003/R004/R007/R008 | propose edits using the configured repair mode |

A deterministic fix is reproducible, but its choice of connection need not match the user's intent. Candidates are revalidated. In the default manual mode, all proposals require acceptance, including quick fixes. Explicit Auto mode permits application without a separate acceptance step and enables the closed loop. Validation itself never applies repairs.

---

## adding a new rule

1. Add a `_check_*` function in `backend/app/validation/rules.py` following the existing pattern.
2. Call it from `validate()`.
3. **State its class (A/B) and, for class B, the soundness-implication.** If it is not a sound under-approximation of a real property — if firing does not imply a certain defect — it does not belong in tier 1. Route the heuristic to tier 2 or tier 3 instead.
4. Assign the next available rule id (R009, ...) with `error` severity and a written rationale.
5. Place it under the right class header here.
6. Add a minimal fixture under `data/rule_cases/` and a smoke test in `backend/tests/test_validation_rules.py`.
7. When a *safe* deterministic quick-fix exists, register it; otherwise use the LLM repair path. Approval remains controlled by the selected mode.
