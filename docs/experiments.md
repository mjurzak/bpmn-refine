# experiments

The experiment pipeline evaluates fixed system configurations on versioned,
deterministic panels sampled from the complete BPMN benchmark.

This doc owns:

- the authoritative `ExperimentConfig` request schema,
- the metric catalogue (what is measured and why),
- the reproducibility contract,
- the ablation plan.

It does **not** own the inputs. How the benchmark is built — operator catalogue, grounding, ground-truth derivation — is [`evaluation/METHODOLOGY.md`](../evaluation/METHODOLOGY.md); the on-disk layout is [`data/eval/README.md`](../data/eval/README.md); the runs themselves are [`experiments/README.md`](../experiments/README.md). [`evaluation/README.md`](../evaluation/README.md) is the map.

Interactive API routes and batch runners share backend services. The generic sweep calls validation and repair services directly and records run metadata; it does not send HTTP requests to a running FastAPI server. E7 and E8 use dedicated drivers for repair and chat refinement. See [`run.md`](run.md) and [`experiments/README.md`](../experiments/README.md).

---

## `ExperimentConfig` — authoritative schema

Resolved for every request that touches validation, repair, or chat. The
frontend exposes provider, model, and reasoning controls plus fixed validation
actions. Experiment specs and the CLI expose the complete schema.

```
ExperimentConfig {
  // model selection
  model_tier:         "strong" | "fast" | "custom"
  model_override?:    str                 // explicit model id when tier = custom
  provider_override?: str                 // built-in or custom registered provider name

  // IR surface
  ir_format:          "pydantic" | "pydantic_json" | "yaml" | "mermaid" | "compact_json"

  // validation
  tiers_enabled:      { t1: bool, t2: bool, t3: bool }
  include_formal_evidence: bool           // default true; false withholds counterexamples
  include_reference_description: bool     // default true
  include_semantic_projection: bool       // default false
  llm_validation_scope: "semantic" | "holistic"

  // repair
  repair_mode:        "atomic" | "regen"
  repair_loop_policy?: "legacy_all_findings" | "target_scoped_safe"
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
- `provider_override` lets a single experiment force e.g. Claude vs GPT without touching `.env`; custom names remain valid when their adapter is registered.
- The deliberate CLI comparison scope is `codex_cli` and `claude_cli`: GPT/OpenAI
  and Claude/Anthropic are the thesis focus because compute and budget are
  limited. Gemini CLI and Antigravity/agy are intentionally out of scope.
- `include_formal_evidence` withholds the counterexample traces, dead elements, and uncovered places from every prompt, while the checker still runs and its verdict still travels. That separates what the *evidence* contributes from what the *check* contributes. Turning the checker off instead would confound the two.
- `include_reference_description` controls whether the matching process text is
  included in semantic validation. `include_semantic_projection` adds the
  compact relation-oriented projection used by the corresponding ablation.
- `llm_validation_scope` selects the closed semantic taxonomy or the broader
  holistic validation prompt.
- Omitting `repair_loop_policy` preserves historical run hashes and the E7 loop
  behavior. New safety-focused runs opt into `target_scoped_safe`, which freezes
  the initial targets and rejects Tier 1 or Woflan regressions.
- `temperature` defaults to **`null`**, meaning "whatever the model defaults to". Set it explicitly only for models that support it; unsupported requested controls fail instead of being silently substituted.
- `seed` is best-effort — not all providers honour it. Anthropic's Messages API has no sampling seed at all. A requested control the selected provider cannot forward is recorded in `LlmTrace.unsupported_controls` rather than reported as if it had applied, so a record never implies a seeded run that never happened.

### config hashing

`config_hash = sha256(canonical_json(ExperimentConfig))[:12]`

Canonical JSON means keys sorted, no whitespace, null fields dropped. The hash is emitted in `run.config_hash` and is the join key between a result and its configuration.

---

## metrics

All metrics are computed post-hoc from `run` data plus endpoint outputs — the dispatcher is not instrumented beyond what already appears in responses.

### validation quality (tier 1, 2, 3)

| Metric | Definition | Source |
|---|---|---|
| **target-category recall** | injected categories recovered by at least one finding | compare `/validate` issues with mutation truth |
| **target-anchor recall** | injected defects with at least one finding on an expected element | compare `element_refs` with construction anchors |
| **category accuracy given anchor** | correct category among localized targets | separates taxonomy from localization |
| **control alert / false-positive rate** | findings on controls | semantic controls remain alerts unless exhaustively adjudicated |
| **tier attribution** | target findings unique to each tier | isolate per-tier runs (`tiers_enabled` toggled) |

Semantic mutation truth is positive-unlabeled: it certifies injected defects but
does not certify that no other semantic problem exists. Unmatched semantic
findings are therefore unadjudicated rather than automatic false positives.
Conventional precision and F1 are valid only for exhaustively annotated groups
or as explicitly labelled conservative lower-bound views.

### repair quality

| Metric | Definition | Rationale |
|---|---|---|
| **target removal** | assigned injected findings absent after repair | primary defect-resolution measure |
| **post-validation safety** | no new Tier 1 or Woflan error | guards against superficially successful edits |
| **iterations and stop reason** | loop work plus its explicit termination cause | distinguishes success, no progress, repetition, and regression rejection |
| **operation count** | number of accepted atomic edits | descriptive change-size measure |
| **model share of repair** | fraction of `applied_op_origins` that is not `quick_fix` | R001/R002/R005/R006 are repaired deterministically; without this split a cross-model comparison credits the model for edits no model made |
| **preservation / unnecessary change** | expected unaffected elements retained | detects collateral changes |
| **manual semantic validity** | reviewed alternative repairs accepted or rejected | covers valid solutions that differ from the inverse mutation |

Graph-edit distance remains a secondary descriptive measure for refinement. It
must not require byte identity with the stored reference because multiple BPMN
repairs can be valid.

### efficiency

| Metric | Definition |
|---|---|
| **wall-clock per iteration (ms)** | response latency of a single `/repair` iteration |
| **tokens in / tokens out** | per LLM call, summed per repair |
| **token reduction vs BPMN XML** | `1 - tokens(ir_format) / tokens(bpmn_xml)` for the same diagram |

Capture is delivered. Every trial record carries `duration_ms` per phase and per call, provider and harness version, requested and effective reasoning/token controls, unsupported controls, provider-reported `usage`, `prompt_chars`, and `input_bytes` — the raw BPMN XML size the reduction is stated against.

Token counts are what the provider reported, not an estimate. The reporting conventions differ in a way that matters when summing: Anthropic excludes cache reads and writes from `input_tokens`, so the billed prompt is `input_tokens + cached_input_tokens`, while OpenAI and Gemini include them, so `cached_input_tokens` is a subset rather than an addend. Each row records its convention in `usage.source`. A call whose response carried no usage block goes into `calls_missing_usage` rather than counting as zero — otherwise a total that is really a lower bound reads as complete.

The analyzer preserves the raw provider total as `tokens` and adds
`comparable_total_tokens`. The latter adds cached input only for Anthropic and
Claude Code, and is the token field used for cross-provider ranking.

An Ollama token count is not comparable to a hosted one: the tokenizer is the local model's. Those rows are tagged `source: "ollama"` for that reason.

CLI runs reuse normal saved CLI authentication, do not probe login state at
startup, and should pin Codex/Claude Code versions for reproducibility. The
adapters isolate each request and disable customizations. Claude's tools are
disabled, and Claude Code prompt suggestions are disabled so the harness does
not add a follow-up suggestion. Claude Code has a five-agentic-turn bound to
complete or correct its structured-output tool call; this remains one user request.
Codex runs read-only and is explicitly instructed not to invoke its tools. Both
serialize history when a native messages API is unavailable. Their
CLI version is part of the experiment environment, not the model name, so
registration reads it with `--version` and each call stores it as
`provider_version`. Claude effort-related environment overrides are cleared;
the `none` stratum explicitly disables thinking.

Mermaid ~93% reduction against raw BPMN XML is the reference point (Grohs et al., 2024). YAML is the novel candidate; Mermaid is the strong-baseline candidate.

### Evaluation and human review

LLM-as-a-Judge was considered but is not used to score the executed E1–E9 experiments. Detection and change assessment use deterministic checks against the versioned ground truth. E7 and E8 also record human review of flagged semantic repairs and refinements. LLM assistance during dataset construction is a separate activity, documented in [`evaluation/METHODOLOGY.md`](../evaluation/METHODOLOGY.md). Executed analyses and review provenance are listed in [`experiments/README.md`](../experiments/README.md).

---

## corpora

**Owned by [`evaluation/METHODOLOGY.md`](../evaluation/METHODOLOGY.md), not by this file.** Where the two disagree about what the inputs are, the methodology wins; this section carries only what the runner needs to know.

One corpus: the **PMo Dataset** (Brissard et al., 2025), 55 human-authored or expert-validated process models with English descriptions, vendored from Zenodo into `data/pmo-dataset/` and gitignored. PMo Benchmark pairs 01–20 and PET-7 pairs 49–54 are subsets of this corpus, not independent datasets.

The benchmark the sweeps actually read is *derived* from PMo by defect injection, not by preprocessing: `evaluation/generator/` emits `data/eval/<version>/`, and the layout contract it has to keep is [`data/eval/README.md`](../data/eval/README.md). The dataset version is declared in the spec and recorded in the manifest.

---

## reproducibility contract

A result is auditable and rerunnable when these anchors are present:

1. `run.config_hash` and the embedded `run.config` pin `ExperimentConfig`.
2. `run.prompt_versions` pin every invoked prompt by name and content hash.
3. `run.model_used`, provider/harness version, and recorded unsupported controls
   identify the effective model surface.
4. `run.converter`, `run.rules_version`, and `run.checkers` pin deterministic
   implementation surfaces.
5. The experiment manifest records `dataset_version`, resolved input paths, and
   byte hashes.
6. `run.app_commit` and the archival run record identify the application state
   and any declared harness drift.

These anchors make the execution trace recoverable; they do not make an
unseeded remote model deterministic. Repeatability is measured separately where
needed. Provider-free generator and analysis steps should be deterministic.

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
| **counterexample prompting** | `include_formal_evidence` | does giving the model the checker's evidence improve repair over giving it the verdict alone |

Each ablation fixes all other fields. The exact panel sizes, selections, model
identifiers, and harness settings are recorded in the executed specs rather than
restated here. Full-corpus Tier 1 and Tier 2 evaluation does not consume model
quota.

The exact executed specifications live in `experiments/specs/runs/`; see
[`experiments/README.md`](../experiments/README.md).

---

## running a sweep

```bash
# rehearse first: canned responses, no API call, proves the spec resolves
make experiment-rehearse SPEC=experiments/specs/runs/smoke.yaml OUT=/tmp/rehearsal

# then for real; --out is where results.jsonl and manifest.json land
make experiment SPEC=experiments/specs/runs/e1-model-selection.yaml OUT=experiments/results/e1-model-selection

# bounded parallel execution; the manifest records the chosen concurrency
make experiment SPEC=experiments/specs/runs/e1-model-selection.yaml \
  OUT=experiments/results/e1-model-selection CONCURRENCY=2
```

Re-running the same `SPEC`/`OUT` pair **resumes**: trials already on disk are skipped. A sweep killed by a rate limit at trial 40 keeps its 40 results and picks up at 41. Concurrency is bounded inside one runner process; multiple processes must not share an output directory.

### the spec

```yaml
experiment_id: ir-format
dataset_version: v1.0         # names the corpus; recorded in the manifest
inputs:                       # literal paths or globs, resolved against --root
  - data/eval/v1.0/variants/**/*.bpmn
description_root: data/eval/v1.0/descriptions/variants
                              # mirrors paths below variants/; required by semantic sweeps
exclude:                      # curation criteria belong in the spec, where the
  - data/eval/v1.0/variants/single/S03/07.bpmn   # manifest records them
base:                         # ExperimentConfig fields shared by every trial
  model_tier: strong
axes:                         # expanded as a full cartesian product over `base`
  ir_format: [pydantic, yaml, mermaid]
configs: []                   # explicit configs appended after the product
repeats: 3                    # identical trials, for variance across a
                              # non-deterministic model
```

Trial count is `inputs × configs × repeats`. Each trial gets a deterministic `trial_id` derived from the experiment id, input path, optional description path, canonical config, and repeat index — so adding an axis value or reordering the spec does not invalidate results already on disk. The application commit is deliberately *not* in the id: a rebuilt binary should not silently re-run a completed sweep, and the manifest records the commit so a mismatch is noticeable.

### how a run is stored

`trials/<trial_id>.json` is the durable checkpoint. Each completed trial is written
to a temporary file, flushed, and atomically renamed, so concurrent completions do
not contend for one append stream and an interrupted write cannot create a partial
record. On resume, the filenames identify completed trials without scanning a large
log. Existing append-only `results.jsonl` runs are imported automatically.

`results.jsonl` remains the analysis interface: after a run, the checkpoint files
are exported in deterministic spec order as one JSON object per line:

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

Three properties of this record matter for the analysis:

- **Failed trials are written, not dropped.** A missing trial looks exactly like one that was never scheduled, and that is how a sweep ends up reporting a mean over a sample it selected for success. `error` carries the exception.
- **Tokens are attributed per phase.** A single total cannot say whether the counterexample evidence made prompts more expensive, which is one of the questions the ablation exists to ask.
- **`final_diagram` is IR, not XML.** The exporter drops constructs the IR cannot hold, so a graph edit distance measured over the export would count those omissions as repair edits. Compute GED on the IR.

Full prompts and responses are withheld unless `--keep-payloads` is passed; `prompt_chars` is recorded either way, since it is the token-reduction denominator.

`manifest.json` records the spec, the application commit, resolved inputs and reference descriptions with their hashes, every config hash, concurrency, checkpoint directory, and cumulative completed / failed counts. `executed_this_run` and `last_invocation` keep a resume-only invocation distinct from the state of the complete experiment.

Analysis scripts read the JSONL and join on `run.config_hash`. No post-hoc instrumentation is allowed — if a metric requires data not in the record, the application is changed first and the sweep is re-run.

---

## outside scope

- No human-subject usability study lives here; the qualitative UX study for Chapter 7 is described in the thesis doc, not the backend docs.
- No online / production monitoring. Everything in this doc is batch evaluation of pre-collected corpora.
- No fine-tuning. The system uses frozen provider models; the experimental lever is prompting, IR, and dispatcher configuration.
