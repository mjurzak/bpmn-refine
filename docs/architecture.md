# architecture

## overview

The system is a full-stack web application split into a React frontend and a FastAPI backend. The backend is further divided into three independent layers that communicate only through defined interfaces.

```
browser
  └── React + bpmn-js (port 5173 / nginx :80)
        │  HTTP /api/v1/*
        ▼
  FastAPI (port 8000)
    ├── /diagrams   — parse / export BPMN XML
    ├── /validate   — run rule engine, optionally call LLM
    └── /chat       — multi-turn conversational refinement
        │
        ├── model/          diagram model + converter
        ├── validation/     deterministic rules (no LLM)
        └── llm/            Anthropic client + routing
```

## layers

### model layer (`backend/app/model/`)

Owns the in-memory representation of a BPMN diagram and the conversion to/from XML.

- `schema.py` — `BpmnDiagram`, `BpmnProcess`, `FlowNode`, `SequenceFlow` (Pydantic models)
- `protocol.py` — `DiagramConverter` Protocol: `parse(bytes) -> Any`, `serialize(Any) -> bytes`
- `registry.py` — maps converter names to instances; reads `DIAGRAM_CONVERTER` from env
- `formats/pydantic_ir.py` — default implementation of the protocol

The converter is swappable at runtime via the `DIAGRAM_CONVERTER` environment variable. See [`converter-format.md`](converter-format.md).

### validation layer (`backend/app/validation/`)

Pure Python, zero LLM dependency. Accepts a `BpmnDiagram` and returns a `ValidationReport`.

Rules are numbered R001–R011 and cover structural constraints (missing events, orphaned nodes, gateway branches, dangling references, duplicate IDs). See [`validation-rules.md`](validation-rules.md).

### llm layer (`backend/app/llm/`)

All LLM calls go through `client.py` — nowhere else in the codebase imports `anthropic` directly.

- `client.py` — `complete()` and `complete_with_history()` wrappers around the Anthropic SDK
- `router.py` — `TaskType` enum → model selection (strong vs fast tier)
- `prompts/` — versioned plain-text prompt files (validate, repair, chat_system)

See [`llm-integration.md`](llm-integration.md).

## data flow — validation request

```
1. client uploads .bpmn file
2. /diagrams/upload  →  PydanticConverter.parse(xml_bytes)  →  BpmnDiagram
3. /validate         →  rules.validate(diagram)             →  ValidationReport
4. (optional)           llm_client.complete(prompt+diagram)  →  semantic issues
5. response: { is_valid, issues, semantic_issues }
```

## data flow — conversational refinement

```
1. client sends { messages, diagram, issues }
2. /chat prepends diagram JSON + issue list to the first message
3. llm_client.complete_with_history(messages) → reply text
4. reply is scanned for ```diagram ... ``` fences
5. if found: BpmnDiagram is parsed and returned as updated_diagram
6. frontend re-imports updated XML into bpmn-js
```

## design principles

- **deterministic rules first, LLM second** — rules are cheap, fast, and fully testable
- **no silent mutations** — the frontend only applies an updated diagram when the user explicitly triggers it
- **swappable representation** — the converter Protocol allows the model layer to evolve without touching the API or validation layers
- **versioned prompts** — prompt text lives in files under `llm/prompts/`, not in source code
