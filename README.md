# bpmn-ai-validator

Interactive system for validation, repair, and refinement of BPMN 2.0 diagrams using Large Language Models.

Part of a master's thesis: *Design and Implementation of an Interactive System Supporting Validation, Repair, and Refinement of BPMN Diagrams with Large Language Models* — AGH University of Science and Technology.

## What it does

- **Validates** BPMN diagrams using deterministic structural rules (R001–R011) and optional LLM-based semantic analysis
- **Repairs** diagrams by proposing targeted fixes for detected issues
- **Refines** diagrams through a conversational interface — users describe intent, the system suggests changes

## Architecture

```
frontend/          React + bpmn-js — diagram editor, validation panel, chat
backend/app/
  model/           BPMN diagram model + swappable converter abstraction
  validation/      deterministic rule engine (no LLM dependency)
  llm/             centralized Anthropic client, model routing, versioned prompts
  api/             FastAPI endpoints: /diagrams, /validate, /chat
```

See [`docs/architecture.md`](docs/architecture.md) for a detailed walkthrough.

## Quick start

**Requirements:** Python 3.12+, Node 18+, a `.env` file (copy from `.env.example`).

```bash
# backend
uv venv .venv && source .venv/bin/activate
uv pip install fastapi "uvicorn[standard]" pydantic pydantic-settings anthropic lxml python-multipart httpx
PYTHONPATH=backend uvicorn app.main:app --reload   # → http://localhost:8000

# frontend (separate terminal)
cd frontend && npm install && npm run dev           # → http://localhost:5173

# or full-stack via Docker
docker compose up --build
```

## Running tests

```bash
PYTHONPATH=backend .venv/bin/pytest tests/ -v
```

## CLI

Run the backend-local CLI without starting FastAPI:

```bash
PYTHONPATH=backend .venv/bin/python -m app.cli validate data/pmo-dataset/bpmn/01.bpmn
PYTHONPATH=backend .venv/bin/python -m app.cli validate data/pmo-dataset/bpmn/01.bpmn --json
PYTHONPATH=backend .venv/bin/python -m app.cli roundtrip data/pmo-dataset/bpmn/01.bpmn --out /tmp/roundtrip.bpmn
PYTHONPATH=backend .venv/bin/python -m app.cli chat data/pmo-dataset/bpmn/01.bpmn --message "add a manager approval step" --out /tmp/refined.bpmn
PYTHONPATH=backend .venv/bin/python -m app.cli repair data/pmo-dataset/bpmn/01.bpmn --out /tmp/repaired.bpmn
PYTHONPATH=backend .venv/bin/python -m app.cli batch-validate data/pmo-dataset/bpmn --recursive --format jsonl --out /tmp/validation.jsonl
```

After `uv pip install -e .`, the CLI is also exposed as:

```bash
bpmn-ai-validator validate data/pmo-dataset/bpmn/01.bpmn
```

Experiment-oriented commands support metadata such as runtime, dataset path, provider, model, and prompt filename:

```bash
bpmn-ai-validator validate data/pmo-dataset/bpmn/01.bpmn --json
bpmn-ai-validator repair data/pmo-dataset/bpmn/01.bpmn --json
bpmn-ai-validator batch-validate data/pmo-dataset/bpmn --recursive --format json --out /tmp/results.json
```

## Documentation

| Document | Description |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | System design, module responsibilities, data flow |
| [`docs/validation-rules.md`](docs/validation-rules.md) | All deterministic validation rules (R001–R011) |
| [`docs/llm-integration.md`](docs/llm-integration.md) | LLM client layer, model routing, prompt versioning |
| [`docs/converter-format.md`](docs/converter-format.md) | Diagram model, converter Protocol, adding new formats |
