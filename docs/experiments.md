# experiments

Phase 3 of the thesis evaluates the system on held-out BPMN corpora.

> **Status.** The **runner, the reproducibility contract, and the efficiency capture are delivered** — see [running a sweep](#running-a-sweep). The **metric catalogue, corpora, and ablation results are still planned**: what exists is the machinery that records the data the metrics are computed from, not the analysis scripts. Sections below are marked where they differ.

This doc owns:

- the authoritative `ExperimentConfig` request schema,
- the metric catalogue (what is measured and why),
- the reproducibility contract,
- the ablation plan.

It does **not** own the inputs. How the benchmark is built — operator catalogue, grounding, ground-truth derivation — is [`evaluation/METHODOLOGY.md`](../evaluation/METHODOLOGY.md); the on-disk layout is [`data/eval/README.md`](../data/eval/README.md); the runs themselves are [`experiments/README.md`](../experiments/README.md). [`evaluation/README.md`](../evaluation/README.md) is the map.

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
  include_formal_evidence: bool           // default true — the neuro-symbolic hinge

  // repair
  repair_mode:        "atomic" | "regen"
  max_repair_iters:   int                 // default 5

  // LLM call parameters
  temperature?:       float               // default null — see below
  reasoning_effort?:  "none" | "low" | "medium" | "high" | "xhigh"
  seed?:              int                 // provider-dependent

  // experiment bookkeeping
  experiment_id?:     str                 // free-form tag for grouping runs
  notes?:             str                 // human-readable context
}
```

### notes on individual fields

- `model_tier` + `model_override` are resolved by [`llm-integration.md`](llm-integration.md) routing. `custom` requires `model_override`.
- `provider_override` lets a single experiment force e.g. Claude vs GPT without touching `.env`.
- `t2_tools` is the ablation lever for [`formal-checkers.md`](formal-checkers.md). **As delivered, tier 2 is Woflan alone**, so this field currently has no subset to vary — see the ablation plan below.
- `include_formal_evidence` withholds the counterexample traces, dead elements, and uncovered places from every prompt **while the checker still runs and its verdict still travels**. That isolates the contribution of the *evidence* from the contribution of the *check*; turning the checker off instead would confound the two.
- `temperature` defaults to **`null`**, meaning "whatever the model defaults to". A fixed `0.0` was the earlier default and was wrong: several current reasoning models reject an explicitly set temperature, including `0.0`, so the configuration declared a value the run never used. Set it to request one; a model that refuses now fails the call rather than silently substituting its own.
- `seed` is best-effort — not all providers honour it. Anthropic's Messages API has no sampling seed at all. A requested control the selected provider cannot forward is recorded in `LlmTrace.unsupported_controls` rather than reported as if it had applied, so a record never implies a seeded run that never happened.

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
| **model share of repair** | fraction of `applied_op_origins` that is not `quick_fix` | R001/R002/R005/R006 are repaired deterministically; without this split a cross-model comparison credits the model for edits no model made |
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

**Capture is delivered.** Every trial record carries `duration_ms` per phase and per call, provider-reported `usage` per call, `prompt_chars` per call, and `input_bytes` — the raw BPMN XML size that the reduction is stated against. The aggregation script is still to write.

Token counts are what the provider reported, not an estimate, and the reporting conventions differ in a way that matters when summing: **Anthropic excludes cache reads and writes from `input_tokens`** (so the billed prompt is `input_tokens + cached_input_tokens`), while **OpenAI and Gemini include them** (so `cached_input_tokens` is a subset, not an addend). Each row records which convention produced it in `usage.source`. A call whose response carried no usage block is counted in `calls_missing_usage` rather than as zero — otherwise a total reads as complete when it is a lower bound.

An Ollama token count is not comparable to a hosted one: the tokenizer is the local model's. Those rows are tagged `source: "ollama"` for that reason.

Mermaid ~93% reduction against raw BPMN XML is the reference point (Grohs et al., 2024). YAML is the novel candidate; Mermaid is the strong-baseline candidate.

### LLM-as-a-Judge (qualitative)

A separate strong-tier LLM call rates repaired diagrams on a small rubric (label fidelity, structural plausibility, BPMN-idiomatic) against the ground truth. Used as a **secondary** signal, never in place of the objective metrics above. Prompt and rubric versioned alongside other prompts; the judge's model id is recorded in `run`.

---

## corpora

**Owned by [`evaluation/METHODOLOGY.md`](../evaluation/METHODOLOGY.md), not by this file.** Where the two disagree about what the inputs are, the methodology wins; this section carries only what the runner needs to know.

One corpus: the **PMo Dataset** (Brissard et al., 2025), 55 human-authored or expert-validated process models with English descriptions, vendored from Zenodo into `data/pmo-dataset/` and gitignored. It already subsumes the two sets an earlier version of this doc listed separately — PMo Benchmark is pairs 01–20 and PET-7 is pairs 49–54 — so they are not independent corpora and counting them as three overstates the coverage.

~~SAP-SAM subset~~ — **dropped.** It was listed for observational metrics over a large unlabelled set. It was never fetched, no metric in the catalogue above needs an unlabelled corpus, and the token-reduction curve is measured per input against `input_bytes` on the labelled set anyway. Recorded as a scope decision.

The benchmark the sweeps actually read is *derived* from PMo by defect injection, not by preprocessing: `evaluation/generator/` emits `data/eval/<version>/`, and the layout contract it has to keep is [`data/eval/README.md`](../data/eval/README.md). The dataset version is declared in the spec and recorded in the manifest.

---

## reproducibility contract

A result is reproducible iff:

1. `run.config_hash` is present — recovers `ExperimentConfig`.
2. `run.prompt_versions` are present — recovers the exact prompt file content via the repo's prompt registry.
3. `run.model_used` and `run.converter` pin the provider-side and IR-side surfaces.
4. `run.rules_version` pins tier 1 (and tier 2's tool set + versions when wired).
5. The dataset snapshot pins the input. Two fields carry it, both in the experiment manifest and neither inside `run`: `input_hashes` pins the bytes of every resolved file, and `dataset_version` — declared by the spec — names the corpus they were drawn from. The hashes alone are not enough, because a sweep over development fixtures and a sweep over the benchmark produce structurally identical manifests.

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
| ~~**checker stack**~~ | ~~`t2_tools` (each subset)~~ | **dropped** — tier 2 ships one checker (Woflan), so there is no subset to vary. Building the Analyzer 2.0 adapter for this ablation alone is the most expensive remaining item, and the counterexample ablation below already carries the neuro-symbolic claim. Recorded as a scope decision. |
| **counterexample prompting** | `include_formal_evidence` | direct test of the neuro-symbolic hinge |

Each ablation is run with **fixed** other fields and sufficient trials to get a confidence interval (N=10 default; higher where provider non-determinism is material).

Specs for tier contribution, IR format, counterexample evidence, and repair budget live in [`experiments/specs/`](../experiments/specs/). **They currently resolve to development fixtures, not to the benchmark** — dataset `v1.0.0` is not generated yet, and each spec declares `dataset_version: dev-fixtures` so a manifest cannot hide that. [`experiments/README.md`](../experiments/README.md) records the two-line migration.

---

## running a sweep

```bash
# rehearse first: canned responses, no API call, proves the spec resolves
make experiment-rehearse SPEC=experiments/specs/smoke.yaml OUT=/tmp/rehearsal

# then for real; --out is where results.jsonl and manifest.json land
make experiment SPEC=experiments/specs/ir_format.yaml OUT=experiments/results/ir-format
```

Re-running the same `SPEC`/`OUT` pair **resumes**: trials already on disk are skipped. A sweep killed by a rate limit at trial 40 keeps its 40 results and picks up at 41.

### the spec

```yaml
experiment_id: ir-format
dataset_version: v1.0.0       # names the corpus; recorded in the manifest
inputs:                       # literal paths or globs, resolved against --root
  - data/eval/v1.0.0/variants/*.bpmn
exclude:                      # curation criteria belong in the spec, where the
  - data/eval/v1.0.0/variants/07_S03_f1.bpmn   # manifest records them
base:                         # ExperimentConfig fields shared by every trial
  model_tier: strong
axes:                         # expanded as a full cartesian product over `base`
  ir_format: [pydantic, yaml, mermaid]
configs: []                   # explicit configs appended after the product
repeats: 3                    # identical trials, for variance across a
                              # non-deterministic model
```

Trial count is `inputs × configs × repeats`. Each trial gets a deterministic `trial_id` derived from the experiment id, input path, canonical config, and repeat index — so adding an axis value or reordering the spec does not invalidate results already on disk. The application commit is deliberately *not* in the id: a rebuilt binary should not silently re-run a completed sweep, and the manifest records the commit so a mismatch is noticeable.

### how a run is stored

`results.jsonl` — one JSON object per trial, appended and flushed as it completes:

```
{
  "trial_id":     "…",           // join key; also the run block's request_id
  "experiment_id":"ir-format",
  "input_path":   "data/…bpmn",
  "input_hash":   "…",           // dataset edits are detectable
  "input_bytes":  4096,          // raw-XML baseline for token reduction
  "repeat":       0,
  "run":          { … full run block, config included … },
  "pre_validation":  { "is_valid": …, "issue_ids": […], "issues": [ … ] },
  "unsupported_elements": [ … ],  // what the import read past without representing
  "unsupported_warning":  null,   // the same, as one sentence
  "repair":       { "iterations": …, "converged": …, "stop_reason": …,
                    "applied_ops": […], "applied_op_origins": […],
                    "failed_ops": […] },
  "post_validation": { … same tiers re-run over the repaired diagram … },
  "phases":       { "validate": {…}, "repair": {…}, "revalidate": {…} },
  "usage":        { "input_tokens": …, "calls_missing_usage": 0, … },
  "final_diagram":{ … the repaired IR … },
  "error":        null
}
```

Three properties are load-bearing:

- **Failed trials are written, not dropped.** A missing trial is indistinguishable from one that was never scheduled, which is how a sweep silently reports a mean over a sample it selected for success. `error` carries the exception.
- **Tokens are attributed per phase.** A single total cannot answer whether the counterexample evidence made prompts more expensive, which is one of the questions the ablation exists to ask.
- **`final_diagram` is IR, not XML.** The exporter drops constructs the IR cannot hold, so a graph edit distance measured over the export would count those omissions as repair edits. Compute GED on the IR.

Full prompts and responses are withheld unless `--keep-payloads` is passed; `prompt_chars` is recorded either way, since it is the token-reduction denominator.

`manifest.json` records the spec, the application commit, resolved inputs with their hashes, every config hash, and the executed / skipped / failed counts.

Analysis scripts read the JSONL and join on `run.config_hash`. No post-hoc instrumentation is allowed — if a metric requires data not in the record, the application is changed first and the sweep is re-run.

---

## outside scope

- No human-subject usability study lives here; the qualitative UX study for Chapter 7 is described in the thesis doc, not the backend docs.
- No online / production monitoring. Everything in this doc is batch evaluation of pre-collected corpora.
- No fine-tuning. The system uses frozen provider models; the experimental lever is prompting, IR, and dispatcher configuration.
