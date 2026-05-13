# experiments *(planned)*

Phase 3 of the thesis evaluates the system on held-out BPMN corpora. This doc owns:

- the authoritative `ExperimentConfig` request schema,
- the metric catalogue (what is measured and why),
- the corpora the evaluation runs against,
- the reproducibility contract,
- the ablation plan.

The system is designed so the **same backend** serves interactive sessions and batch ablation runs. An experiment is not a separate code path — it is a sweep over `ExperimentConfig` that hits the live endpoints and records every `run` block. See [`run.md`](run.md).

---

## `ExperimentConfig` — authoritative schema

Carried on **every** request that touches validation, repair, or chat. The frontend exposes these as a configuration panel; the batch runner iterates over the same fields.

```
ExperimentConfig {
  // model selection
  model_tier:         "strong" | "fast" | "custom"
  model_override?:    str                 // explicit model id when tier = custom
  provider_override?: str                 // "anthropic" | "openai" | "ollama"

  // IR surface
  ir_format:          "pydantic" | "pydantic_json" | "yaml" | "mermaid" | "compact_json"

  // validation
  tiers_enabled:      { t1: bool, t2: bool, t3: bool }
  t2_tools?:          ["bpmn_analyzer", "woflan", "bpmnspector"]  // default: all enabled

  // repair
  repair_mode:        "atomic" | "regen"
  max_repair_iters:   int                 // default 5

  // LLM call parameters
  temperature:        float               // default 0.0 for reproducibility
  seed?:              int                 // provider-dependent

  // experiment bookkeeping
  experiment_id?:     str                 // free-form tag for grouping runs
  notes?:             str                 // human-readable context
}
```

### notes on individual fields

- `model_tier` + `model_override` are resolved by [`llm-integration.md`](llm-integration.md) routing. `custom` requires `model_override`.
- `provider_override` lets a single experiment force e.g. Claude vs GPT without touching `.env`.
- `t2_tools` is the ablation lever for [`formal-checkers.md`](formal-checkers.md): "run with Analyzer only" vs "full stack".
- `temperature: 0.0` is the default because reproducibility requires it; experiments that deliberately vary temperature record it in `run` like anything else.
- `seed` is best-effort — not all providers honour it. When honoured, it goes into `run.config_hash`; when not, results are treated as non-deterministic and averaged over N trials.

### config hashing

`config_hash = sha256(canonical_json(ExperimentConfig))[:12]`

Canonical JSON means keys sorted, no whitespace, null fields dropped. The hash is emitted in `run.config_hash` and is the join key between a result and its configuration in Phase 3 analysis.

---

## metrics

All metrics are computed post-hoc from `run` data plus endpoint outputs — the dispatcher is not instrumented beyond what already appears in responses.

### validation quality (tier 1, 2, 3)

| Metric | Definition | Source |
|---|---|---|
| **precision** | `\|found ∩ ground_truth\| / \|found\|` | compare `/validate` issues against annotated corpus |
| **recall** | `\|found ∩ ground_truth\| / \|ground_truth\|` | same |
| **F1** | harmonic mean | derived |
| **tier attribution** | fraction of correct findings unique to each tier | isolate per-tier runs (`tiers_enabled` toggled) |

The tier-attribution metric is the one that answers *"is the formal-checker stack worth its cost?"* — if tier 2 adds few uniquely-detected issues over tier 1 + tier 3, the neuro-symbolic claim weakens.

### repair quality

| Metric | Definition | Rationale |
|---|---|---|
| **convergence rate** | fraction of runs with `run.converged = true` | headline correctness number |
| **iterations** | mean `run.iterations` | convergence speed; compare against the Dantas et al. `4/δ` theoretical bound |
| **minimality** | mean `len(applied_ops)` per repair | how surgical is the fix — lower is better when convergence is equal |
| **GED(initial, final)** | graph edit distance from pre-repair to post-repair IR | magnitude of change |
| **GED(final, ground_truth)** | GED from repaired IR to the known-correct diagram | does the repair go where it should |
| **RGED** | relative GED — GED normalised by diagram size | cross-dataset comparison |
| **soundness pass rate** | fraction of repaired diagrams that pass tier 2 | independent check that repair is real, not just tier-1-surface |

GED / RGED implementations follow the metric definitions in the PMo Benchmark and in ProMoAI (Kourani et al., 2024). See `research/reports/Initial-Research.md` §3.

### efficiency

| Metric | Definition |
|---|---|
| **wall-clock per iteration (ms)** | response latency of a single `/repair` iteration |
| **tokens in / tokens out** | per LLM call, summed per repair |
| **token reduction vs BPMN XML** | `1 - tokens(ir_format) / tokens(bpmn_xml)` for the same diagram |

Mermaid ~93% reduction against raw BPMN XML is the reference point (Grohs et al., 2024). YAML is the novel candidate; Mermaid is the strong-baseline candidate.

### LLM-as-a-Judge (qualitative)

A separate strong-tier LLM call rates repaired diagrams on a small rubric (label fidelity, structural plausibility, BPMN-idiomatic) against the ground truth. Used as a **secondary** signal, never in place of the objective metrics above. Prompt and rubric versioned alongside other prompts; the judge's model id is recorded in `run`.

---

## corpora

### PMo Benchmark (primary)

- Paper: *A benchmark suite for LLM process modeling* (PET-7 extension, 2024).
- Content: seeded BPMN diagrams with known faults and ground-truth repairs.
- Role: primary test set for precision / recall / convergence / GED.

### PET-7 (process-modelling prompts)

- Source: natural-language process descriptions + expected BPMN structure.
- Role: evaluates the chat / refinement surface — does conversational generation reach the intended process?

### SAP-SAM subset

- Source: SAP-SAM public BPMN collection.
- Role: large, realistic, **unlabelled** set used for observational metrics (conformance rate under tier 1 / tier 2, token-reduction curves) where ground-truth repairs are not available.

Per-dataset preprocessing (PII scrub, invalid-XML filter) is run once and versioned as a script in `research/` — not re-run per experiment. The dataset snapshot id is recorded in the experiment manifest.

---

## reproducibility contract

A result is reproducible iff:

1. `run.config_hash` is present — recovers `ExperimentConfig`.
2. `run.prompt_versions` are present — recovers the exact prompt file content via the repo's prompt registry.
3. `run.model_used` and `run.converter` pin the provider-side and IR-side surfaces.
4. `run.rules_version` pins tier 1 (and tier 2's tool set + versions when wired).
5. The dataset snapshot id (outside `run`, in the experiment manifest) pins the input.

Given these five anchors, rerunning the same endpoint against the same input should reproduce the output modulo provider non-determinism (temperature, seed honouring). Rerunning is how regressions are caught — the CI harness replays a small fixture suite on every prompt or rules change and fails if metrics drop.

---

## ablation plan

Ablations are `ExperimentConfig` sweeps. Each row below corresponds to a plot or table in the thesis' Chapter 7.

| Ablation | Swept field | Question |
|---|---|---|
| **tier contribution** | `tiers_enabled` (seven non-empty subsets of {t1, t2, t3}) | which tier contributes how much to precision / recall |
| **IR format** | `ir_format` (pydantic-json, yaml, mermaid, compact-json) | does candidate IR choice affect token usage, generation quality, edit success |
| **repair mode** | `repair_mode` (atomic vs regen) | quantify atomic's advantage under this system (not just BPMN Assistant's) |
| **max iterations** | `max_repair_iters` (1, 2, 3, 5, 10) | does convergence actually need multiple rounds, and how many |
| **model tier** | `model_tier` + `model_override` | strong vs fast; cross-provider (Claude vs GPT vs local Llama via Ollama) |
| **checker stack** | `t2_tools` (each subset) | is the full three-tool stack worth it over Analyzer-only |
| **counterexample prompting** | feature flag: strip counterexample vs include it | direct test of the neuro-symbolic hinge |

Each ablation is run with **fixed** other fields and sufficient trials to get a confidence interval (N=10 default; higher where provider non-determinism is material).

---

## how a run is stored

A batch run writes JSONL to `research/experiments/{experiment_id}/results.jsonl`:

```
{
  "input_id":    "pmo_0042",
  "response":    { ... full endpoint response ... },
  "run":         { ... the run block, duplicated here for easy indexing ... },
  "config_hash": "...",
  "timestamp":   "2026-..."
}
```

Analysis notebooks read the JSONL, join on `config_hash`, and produce the plots. No post-hoc instrumentation is allowed — if a metric requires data not in `run` or in the response, the endpoint is changed first and the experiment is re-run.

---

## outside scope

- No human-subject usability study lives here; the qualitative UX study for Chapter 7 is described in the thesis doc, not the backend docs.
- No online / production monitoring. Everything in this doc is batch evaluation of pre-collected corpora.
- No fine-tuning. The system uses frozen provider models; the experimental lever is prompting, IR, and dispatcher configuration.
