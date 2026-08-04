# llm integration

All LLM usage in the system routes through a single facade (`backend/app/llm/client.py`). Concrete SDK imports are confined to `backend/app/llm/providers/`. This doc covers the client facade, the provider abstraction, model routing, prompt versioning, the structured-outputs layer, and how each LLM call contributes to the `run` metadata.

---

## client facade

```python
from app.llm.client import (
    complete,
    complete_with_history,
    complete_structured,
    complete_structured_with_history,
)
```

Four async entry points; all delegate to the provider resolved for the call:

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

await complete_structured(
    prompt: str,
    schema: dict,                  # JSON schema for the required response object
    system: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int = 4096,
) -> Any                           # parsed JSON (dict/list), not a string

await complete_structured_with_history(
    messages: list[dict],
    schema: dict,
    ...
) -> Any
```

No other module imports `anthropic`, `openai`, `google-genai`, or any provider SDK directly. If a new feature needs vendor-specific capability (e.g. tool use, response-format enforcement), it is added here — not called from a route handler.

---

## provider abstraction

`backend/app/llm/protocol.py` defines the `LLMProvider` protocol with matching
single-turn, history, and structured variants. Every provider implements it.

Current providers (`backend/app/llm/providers/`):

| Provider | Module | Notes |
|---|---|---|
| OpenAI | `openai.py` | shipped default provider (see model routing) |
| Anthropic | `anthropic.py` | swappable via `.env` / `ExperimentConfig`; used for cross-model comparison |
| Ollama | `ollama.py` | local/offline runs; subclasses `OpenAIProvider` (OpenAI-compatible endpoint) |
| Gemini | `gemini.py` | Google models via `google-genai`; registered only when `GEMINI_API_KEY` is set (SDK imported lazily) |

Adding a provider: implement the protocol in a new module under `providers/` and register it via `backend/app/llm/registry.py` at startup. No other code changes.

---

## model routing

`backend/app/llm/router.py` exposes a `TaskType` enum and two resolvers (`resolve_model`, `resolve_provider`). Call sites pass a task type rather than hard-coding a model id.

| TaskType | Tier |
|---|---|
| `SEMANTIC_VALIDATION` | strong |
| `REPAIR` | strong |
| `REFINEMENT` | strong |
| `IR_CONVERSION` | fast |
| `SUMMARY` | fast |
| `SIMPLE_QUERY` | fast |

The concrete model behind each tier is set in config, not hard-coded per task. The shipped defaults (`backend/app/core/config.py`) are `LLM_STRONG_MODEL=gpt-5.6-sol` and `LLM_FAST_MODEL=gpt-5.6-luna`; treat the values as configuration and read the current ones from `.env` / `config.py` rather than trusting a copy here. Tier and provider are configurable per-tier via `.env`:

```
LLM_PROVIDER=openai                 # global default
LLM_STRONG_PROVIDER=openai          # optional override for strong tasks
LLM_FAST_PROVIDER=openai            # optional override for fast tasks
LLM_STRONG_MODEL=gpt-5.6-sol
LLM_FAST_MODEL=gpt-5.6-luna
```

Per-request, `ExperimentConfig.model_tier` and `model_override` can supersede the static defaults, which is how ablation experiments (e.g. "run the same task on GPT and Claude") are driven without changing code.

---

## prompts

Prompt text lives in `backend/app/llm/prompts/` as plain `.txt` files. They are read at request time, so edits take effect without restarting the server.

| File | Used by | Purpose |
|---|---|---|
| `validate.txt` | `/validate` (tier 3) | detect semantic issues not catchable by tier 1 / tier 2 |
| `repair.txt` | `/repair`, `repair_mode = regen` | produce a full replacement IR from issues + `tier2_findings` |
| `repair_atomic.txt` | `/repair`, `repair_mode = atomic` | produce an `EditOp` plan under structured outputs |
| `repair_xml.txt` | `/repair/xml` | rewrite BPMN XML that does not parse into the IR |
| `chat_system.txt` | `/chat` | conversational refinement, intent-driven |

### versioning — hash + name, not rename

Each prompt file is loaded lazily and hashed with sha256. The **first 12 hex characters** of that hash, paired with the filename stem, form the entry in `run.prompt_versions`:

```json
"validate": { "name": "validate_v1", "hash": "ab34cd5e78f9" }
```

Rationale: renaming files (`validate_v2.txt`) is discipline-based and brittle. A content hash changes **automatically** whenever the file changes, capturing uncommitted edits experimenters make locally. Renames are still useful for deliberate branching (running `v1` and `v2` side-by-side in a comparison run), but the hash is the reproducibility anchor — not the name. See [`run.md`](run.md).

For a deliberate A/B run, rename the parallel version `prompt_v2.txt`, register it by key, and point `ExperimentConfig` at the chosen key. The hash column in the run block will tell both versions apart regardless.

---

## structured outputs

`complete_structured(...)` and `complete_structured_with_history(...)` constrain
the model's reply to a JSON schema. The four providers each map the same
`schema` onto their native structured-output mechanism:

| Provider | Native mechanism |
|---|---|
| Anthropic | `output_config.format` with `{"type": "json_schema", "schema": ...}` |
| OpenAI | `response_format` json_schema with `strict: true` |
| Ollama | same `response_format` (inherited from `OpenAIProvider`); served by Ollama's `format`/GBNF constrained decoding, so `strict` is left off |
| Gemini | `responseSchema` + `response_mime_type="application/json"` |

The provider returns a JSON string; the client facade decodes it so call sites
get a dict/list. Traces retain both the actual schema sent to the adapter and
the raw structured output.

### common response envelope

Every provider-backed task uses a strict outer contract:

```json
{
  "description": "Human-readable explanation",
  "result": {}
}
```

`description` is required and application validation rejects blank or
whitespace-only values. `result` has a separate model for each task:
semantic findings, atomic edit operations, regenerated IR plus unresolved
issues, an optional chat diagram, or corrected XML. Complete diagrams and XML
stay serialized as strings inside the strict envelope; the normal IR and BPMN
parsers validate their contents after generation.

### schema preparation — `backend/app/llm/schema.py`

Pydantic's raw `model_json_schema()` is not what the strict APIs want, so `strict_json_schema(model_or_adapter)` post-processes it:

- every object gets `additionalProperties: false` and a `required` list covering all declared keys (strict providers reject anything looser);
- pydantic's discriminated-union `oneOf` is rewritten to `anyOf` (the keyword the strict APIs accept).

`inline_defs(schema)` additionally flattens `$ref`/`$defs` for Gemini, which does not resolve references. Both helpers require **non-recursive** schemas — fine for the current IR, since subprocess nesting is out of scope.

The full `BpmnDiagram` is deliberately not used as a provider schema because
`namespaces` and `FlowNode.extra` are open dictionaries needed for round-trip
fidelity. A schema around the serialized string standardizes the response
shape; it does not claim to validate the YAML, Mermaid, compact JSON, canonical
JSON, or XML encoded inside it. That validation remains a separate parser step
with at most one correction round.

---

## the `EditOp` prompt contract

Under `repair_mode = atomic`, the LLM emits a list of edit operations over the canonical IR. The schema is shared with the repair loop ([`repair-loop.md`](repair-loop.md)) and is documented authoritatively there. This doc covers only how it enters the prompt.

`repair_atomic.txt` contains:

- a description of the `EditOp` union (each op type, its fields, its preconditions);
- operation-selection and locality guidance; the provider schema supplies the
  exact response shape;
- constraints that keep edits local (e.g. do not rename unaffected elements).

Under `repair_mode = regen`, `repair.txt` asks for a serialized full replacement
IR inside the common envelope.

---

## counterexample prompting

When tier 2 produces a witness, it is injected into the repair prompt in **structured form** (a `tier2_findings` section built by `_extract_tier2_findings`) — not pasted as raw tool output. This is wired. The gap is the witness content: Woflan supplies a diagnosis but no firing trace, so the `trace`/`marking` fields below are empty in practice today. The prompts already tell the model these may be empty.

Target structure:

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
├── client.py         # single-turn/history, plain/structured entry points
├── envelope.py       # common description + task-specific result contract
├── protocol.py       # LLMProvider protocol
├── registry.py       # provider registration + resolution
├── router.py         # TaskType enum, tier/provider resolvers
├── schema.py         # strict_json_schema / inline_defs for structured outputs
├── providers/
│   ├── anthropic.py
│   ├── openai.py
│   ├── ollama.py
│   └── gemini.py
└── prompts/
    ├── validate.txt
    ├── repair.txt
    ├── repair_atomic.txt
    ├── repair_xml.txt
    └── chat_system.txt
```
