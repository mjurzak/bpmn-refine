# validation rules — tier 1

Tier 1 is the deterministic rule layer. All rules live in `backend/app/validation/rules.py`; they run synchronously, require no external dependencies, and have no LLM dependency.

Tier 1 is one of three validation tiers (see [`architecture.md`](architecture.md) for the full picture):

| Tier | Runs | Catches |
|---|---|---|
| **1 — deterministic rules** | every edit (live) | local structural violations |
| 2 — formal checker stack | on demand | soundness, deadlocks, standards compliance ([`formal-checkers.md`](formal-checkers.md)) |
| 3 — LLM semantic review | on demand | label quality, role mislabelling, process-intent issues ([`llm-integration.md`](llm-integration.md)) |

Tier 1 explicitly does **not** catch deadlocks, livelocks, token-count mismatches, or unreachable states — those require the state-space exploration done by tier 2. Nor does it judge whether a task is *correctly named* for the process — that's tier 3.

---

## severity levels

| Level | Meaning |
|---|---|
| `error` | Diagram is structurally invalid; must be fixed before repair / export |
| `warning` | Potentially problematic but allowed by BPMN 2.0; often worth flagging |
| `info` | Informational; no action required |

Severity choice is a *policy* decision, not a BPMN 2.0 mandate. The rationale column below records why each rule landed where it did.

---

## rules

Grouped by dimension:

### structural — element existence and identity

| ID | Severity | Condition | Rationale |
|---|---|---|---|
| R001 | error | process has no start event | a process with no entry point is unrunnable; explicitly prohibited in most BPMN execution semantics |
| R003 | error | process has no end event | same as R001 for termination — an end event is required for the process to *complete* in the Petri-net sense |
| R011 | error | duplicate element ID within a process | BPMN element IDs must be unique per document; duplicates break downstream tooling |

### connectivity — flow wiring

| ID | Severity | Condition | Rationale |
|---|---|---|---|
| R004 | error | start event has no outgoing sequence flow | a start event with nowhere to go is effectively missing |
| R005 | error | end event has no incoming sequence flow | unreachable end; the process can never terminate through this node |
| R006 | warning | element has neither incoming nor outgoing flows (fully disconnected) | may be legitimate in-progress modelling, hence warning not error |
| R009 | error | sequence flow references an unknown source element ID | dangling reference; the flow cannot be rendered or executed |
| R010 | error | sequence flow references an unknown target element ID | same as R009 |

### gateway semantics — branch and condition sanity

| ID | Severity | Condition | Rationale |
|---|---|---|---|
| R007 | warning | gateway has fewer than 2 outgoing flows | technically allowed but typically a modelling mistake — a gateway with one branch adds no decision logic |
| R008 | warning | exclusive gateway has more than one outgoing flow without a condition expression | ambiguous branching; the process is non-deterministic in a way the modeller probably didn't intend |

### multi-start policy

| ID | Severity | Condition | Rationale |
|---|---|---|---|
| R002 | warning | process has more than one start event | BPMN 2.0 **permits** multiple start events; in practice most well-formed processes have exactly one, so this is a *soft* convention, not a violation |

---

## categories and the repair dispatcher

The category axis above drives the planned repair dispatcher ([`repair-loop.md`](repair-loop.md)). For each category, a fixed set of deterministic quick-fixes is attempted before falling back to an LLM repair:

| Category | Example deterministic fix |
|---|---|
| structural | insert a start / end event |
| connectivity | rewire a dangling reference, or remove the broken flow |
| gateway semantics | change gateway type, add a default flow, prompt the user for a condition |
| multi-start | no deterministic fix — surfaced as a suggestion only |

Adding a rule therefore requires picking (or introducing) a category so the dispatcher knows where to route it.

---

## adding a new rule

1. Add a `_check_*` function in `backend/app/validation/rules.py` following the existing pattern.
2. Call it from `validate()`.
3. Assign the next available rule ID (R012, ...) and a severity with a written rationale (same shape as the tables above).
4. Place it under the right category header in this doc; if it does not fit an existing category, add one — but check whether it's really still tier 1 and not a tier-2 concern.
5. Add a test case in `tests/test_validation_rules.py`.
6. When the repair dispatcher lands, register a deterministic quick-fix for the new rule if a natural one exists.
