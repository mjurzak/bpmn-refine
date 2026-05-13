# llm integration

All LLM usage in the system routes through a single facade (`backend/app/llm/client.py`). Concrete SDK imports are confined to `backend/app/llm/providers/`. This doc covers the client facade, the provider abstraction, model routing, prompt versioning, the planned structured-outputs migration, and how each LLM call contributes to the `run` metadata.

---

## client facade

```python
from app.llm.client import complete, complete_with_history
```

Two async entry points; both delegate to the provider resolved for the call:

```python
await complete(
    prompt: str,
    system: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int = 4096,
) -> str

await complete_with_history(
    messages: list[dict],          # [{"role": "user"|"assistant", "content": "..."}]
    system: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int = 4096,
) -> str
```

No other module imports `anthropic`, `openai`, or any provider SDK directly. If a new feature needs vendor-specific capability (e.g. tool use, response-format enforcement), it is added here — not called from a route handler.

---

## provider abstraction

`backend/app/llm/protocol.py` defines the `LLMProvider` protocol: two async methods (`complete`, `complete_with_history`) with identical signatures to the client facade. Every provider implements it.

Current providers (`backend/app/llm/providers/`):

| Provider | Module | Notes |
|---|---|---|
| Anthropic | `anthropic.py` | primary; strong-tier default |
| OpenAI | `openai.py` | for cross-model comparison experiments |
| Ollama | `ollama.py` | local/offline runs; useful for CI and for experiments that shouldn't hit a cloud |

Adding a provider: implement the protocol in a new module under `providers/` and register it via `backend/app/llm/registry.py` at startup. No other code changes.

---

## model routing

`backend/app/llm/router.py` exposes a `TaskType` enum and two resolvers (`resolve_model`, `resolve_provider`). Call sites pass a task type rather than hard-coding a model id.

| TaskType | Tier | Default |
|---|---|---|
| `SEMANTIC_VALIDATION` | strong | `claude-opus-4-7` |
| `REPAIR` | strong | `claude-opus-4-7` |
| `REFINEMENT` | strong | `claude-opus-4-7` |
| `IR_CONVERSION` | fast | `claude-haiku-4-5` |
| `SUMMARY` | fast | `claude-haiku-4-5` |
| `SIMPLE_QUERY` | fast | `claude-haiku-4-5` |

Tier and provider are configurable per-tier via `.env`:

```
LLM_PROVIDER=anthropic              # global default
LLM_STRONG_PROVIDER=anthropic       # optional override for strong tasks
LLM_FAST_PROVIDER=anthropic         # optional override for fast tasks
LLM_STRONG_MODEL=claude-opus-4-7
LLM_FAST_MODEL=claude-haiku-4-5
```

Per-request, `ExperimentConfig.model_tier` and `model_override` can supersede the static defaults, which is how ablation experiments (e.g. "run the same task on GPT and Claude") are driven without changing code.

---

## prompts

Prompt text lives in `backend/app/llm/prompts/` as plain `.txt` files. They are read at request time, so edits take effect without restarting the server.

| File | Used by | Purpose |
|---|---|---|
| `validate.txt` | `/validate` (tier 3) | detect semantic issues not catchable by tier 1 / tier 2 |
| `repair.txt` | `/repair` (planned) | produce an `EditOp` plan (or a full IR under `repair_mode = regen`) from issues + counterexamples |
| `chat_system.txt` | `/chat` | conversational refinement, intent-driven |

### versioning — hash + name, not rename

Each prompt file is loaded lazily and hashed with sha256. The **first 12 hex characters** of that hash, paired with the filename stem, form the entry in `run.prompt_versions`:

```json
"validate": { "name": "validate_v1", "hash": "ab34cd5e78f9" }
```

Rationale: renaming files (`validate_v2.txt`) is discipline-based and brittle. A content hash changes **automatically** whenever the file changes, capturing uncommitted edits experimenters make locally. Renames are still useful for deliberate branching (running `v1` and `v2` side-by-side in a comparison run), but the hash is the reproducibility anchor — not the name. See [`run.md`](run.md) *(planned doc)*.

For a deliberate A/B run, rename the parallel version `prompt_v2.txt`, register it by key, and point `ExperimentConfig` at the chosen key. The hash column in the run block will tell both versions apart regardless.

---

## structured outputs (planned migration)

Today the LLM returns plain text; diagram payloads come back inside ` ```diagram ... ``` ` fences which the server parses out. This works but is brittle: the model can wrap the fence differently, add commentary, or emit invalid JSON.

The planned migration is to **structured outputs / tool use**, per provider:

- **Anthropic** — tool use with a typed `propose_edits` tool whose input schema matches `EditOp[]`, or a single `replace_diagram` tool under `repair_mode = regen`.
- **OpenAI** — response format with a JSON schema; same shape.
- **Ollama** — depends on the local model's support; fall back to fenced-JSON with a strict parser.

The client facade will gain a third entry point for structured calls (provisional name `complete_structured(prompt, system, schema, ...)`), returning parsed data rather than a string. Each provider implements it with its native SDK feature; Ollama uses the fence fallback.

Until this lands, the `/chat` flow keeps the fenced-diagram convention and `/repair` is **not** yet wired.

---

## the `EditOp` prompt contract

Under `repair_mode = atomic`, the LLM emits a list of edit operations over the canonical IR. The schema is shared with the repair loop ([`repair-loop.md`](repair-loop.md) *(planned)*) and is documented authoritatively there. This doc covers only how it enters the prompt.

`repair.txt` (and, when the user asks for a fix, `chat_system.txt`) contains:

- a description of the `EditOp` union (each op type, its fields, its preconditions);
- an instruction that the model MUST return an op list and nothing else (hard under plain text; trivial under structured outputs);
- constraints that keep edits local (e.g. do not rename unaffected elements).

Under `repair_mode = regen`, the same prompts include a fallback branch asking for a full replacement IR instead.

---

## counterexample prompting (planned)

When tier 2 produces a counterexample (e.g. a token-flow trace leading to a deadlock), it is injected into the repair prompt in **structured form** — not pasted as raw tool output.

Planned structure:

```
## problem
Rule: {rule_id}   severity: {severity}
Message: {message}
Affected elements: {element_refs}

## counterexample
Trace: step 1 -> step 2 -> ... -> deadlock at {node_id}
Marking at deadlock: { node_id: token_count, ... }

## canonical IR
{compact canonical IR in the active candidate format}
```

The LLM reasons about *why* the trace deadlocks and emits an `EditOp` plan that addresses the root cause — not just the symptom. This is the neuro-symbolic hinge point the thesis is built around (see `research/reports/Initial-Research.md` §3).

---

## run metadata contribution

Every LLM call participates in the `run` block on the surrounding response:

- The provider's `complete()` returns the *actual* model id the service answered with (some providers route internally); this feeds `run.model_used`.
- The client facade hashes the prompt file at load time and contributes the `{name, hash}` entry for that prompt file to `run.prompt_versions`.
- `run.request_id` is a per-request UUID, generated upstream in the route handler and propagated into provider calls for correlation with provider-side logs where possible.

Responses never omit `run`. If a provider call fails, the route returns an error response **with `run` still populated** for the portion of the pipeline that did execute.

---

## file map

```
backend/app/llm/
├── client.py         # public entry points (complete, complete_with_history)
├── protocol.py       # LLMProvider protocol
├── registry.py       # provider registration + resolution
├── router.py         # TaskType enum, tier/provider resolvers
├── providers/
│   ├── anthropic.py
│   ├── openai.py
│   └── ollama.py
└── prompts/
    ├── validate.txt
    ├── repair.txt
    └── chat_system.txt
```
