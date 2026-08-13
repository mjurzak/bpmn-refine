# run — response envelope

Every backend response includes a `run` block. It records what produced the response: which model, which prompts, which rules, and which config. Without it, experiment analysis cannot join a metric back to the conditions that produced it.

`run` is a hard contract — **no endpoint omits it**, even on error.

---

## schema

```
run {
  model_used:       str                   // model(s) actually called, "none" if no call was made
  model_configured?: str                  // what the config resolved to, called or not
  prompt_versions:  {
    validate: { name: "validate_v1",    hash: "ab34cd5e78f9" },
    repair:   { name: "repair_v1",      hash: "..." },
    chat:     { name: "chat_system_v1", hash: "..." }
  }
  converter:        str                   // canonical IR version id, e.g. "pydantic_ir@v1"
  rules_version:    str                   // e.g. "R001-R008"
  checkers?:        { woflan: "pm4py-2.7" }
  config_hash:      str                   // 12-char hex prefix of sha256(ExperimentConfig)
  timestamp:        str                   // ISO-8601 UTC
  request_id:       str                   // per-request UUID
  iterations?:      int                   // repair loop only
  converged?:       bool                  // repair loop only
}
```

Fields marked `?` are per-endpoint additions; everything else is always present.

---

## field rationale

### `model_used` and `model_configured`

The provider's response carries the *actual* model id it served. Some providers route internally (model aliases, fallback chains), so the id requested in `ExperimentConfig.model_override` is not necessarily the id that answered. Record what answered, not what was asked.

`model_used` is built from the request's LLM traces, so it names only models a call was actually addressed to — comma-separated if a run spanned more than one, and `"none"` when nothing reached a provider. That case is common and not an edge: a tier-1-only validation, or a repair every issue of which had a deterministic quick fix, makes no call at all. `model_configured` keeps the id the configuration resolved to, so "this model repaired it" stays separable from "this model would have been asked".

Each LLM trace also carries its logical `task` (`repair`,
`semantic_validation`, or `refinement`). This differs from the low-level
completion `kind`: one `/repair` request can contain both repair generation and
semantic revalidation calls even though both use structured completion. The
frontend uses `task` to label those calls accurately.

### `prompt_versions`

Each entry names a prompt file (`name`) and pins its exact content (`hash`). Content hash beats filename-versioning because:

- A content hash changes **automatically** when the file changes — captures uncommitted local edits experimenters make.
- Renames (`validate_v2.txt`) still work: the `name` changes too, and side-by-side A/B runs are distinguishable by `name`, while within a given `name` the `hash` catches silent edits.

Only the prompts used in the current response appear. A `/validate` response does not need `repair` or `chat` entries.

### `converter`

Canonical IR version id. Pins the parser/serializer code path that produced the IR the request operated on. When candidate IRs land ([`converter-format.md`](converter-format.md)), the active candidate is recorded the same way.

### `rules_version`

Tier 1 rule set id. Today: `"R001-R008"`. Mechanical — bumps when a rule is added, removed, or changed. Same shape will accommodate tier 2 once the formal-checker stack is wired; see `checkers` below.

### `checkers` *(added when tier 2 is wired)*

Per-tool version strings. Formal checker findings depend on the tool binary; pinning the binary version is the only way to replay later if a new release changes a verdict.

### `config_hash`

12-char hex prefix of `sha256(canonical_json(ExperimentConfig))`. The join key for experiment analysis. See [`experiments.md`](experiments.md) for the canonicalisation rules.

### `timestamp` and `request_id`

`timestamp` is UTC ISO-8601. `request_id` is a UUID generated in the route handler and propagated into LLM calls, so a response can be correlated with provider-side logs.

### `iterations` and `converged`

Repair-loop-only. `iterations` counts dispatcher rounds; `converged` is `true`
iff `remaining_issues` contains no repairable error or warning. See
[`repair-loop.md`](repair-loop.md).

---

## the 12-char hash

All `hash` fields in `run` are the **first 12 hex characters** of `sha256(bytes)`.

- 12 hex chars = 48 bits = ~2.8 × 10¹⁴ values.
- Probability of collision among 1000 artefacts is ≈ 10⁻¹⁰; among 10 million, still <10⁻⁴. Entirely adequate for a research system.
- Matches git's long-form abbreviation, which is the mental model anyone reading logs already has.
- Full sha256 is always reconstructible from the source file — storing 12 chars loses nothing a human needs.

Inputs:

- **Prompts**: the `.txt` file bytes, read at request time.
- **`ExperimentConfig`**: canonical JSON serialisation (keys sorted, nulls dropped, no whitespace).
- **Converter and rules ids**: not hashed — semantic version strings (`pydantic_ir@v1`, `R001-R008`) are clearer than hashes for code that has a proper version label.

---

## non-omission rule

`run` appears on **every** response from endpoints that own a canonical run context — `/validate`, `/repair`, `/chat`. This includes:

- success responses,
- error responses (populated for whatever part of the pipeline did execute before the error),
- partial responses (e.g. a non-converged repair),
- cached responses (the `run` from the original computation is replayed, not re-generated).

The experiment harness treats a missing `run` as a hard failure.

Endpoints that do not own a canonical run context — `/history/*`, `/diagrams/export` for round-trip-only transforms — do not emit `run`. The contract is per-endpoint and documented on the route.

---
