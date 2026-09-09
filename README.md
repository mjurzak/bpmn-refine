# bpmn-ai-validator

Interactive system for validation, repair, and refinement of BPMN 2.0 diagrams using Large Language Models.

Part of a master's thesis: *Design and Implementation of an Interactive System Supporting Validation, Repair, and Refinement of BPMN Diagrams with Large Language Models* — AGH University of Science and Technology.

## What it does

- **Validates** BPMN diagrams with deterministic structural rules (R001–R008), Petri-net soundness checking, and LLM semantic review
- **Repairs** diagrams by proposing targeted fixes for detected issues
- **Refines** diagrams through chat: the user describes intent, the system suggests changes

Nothing is applied automatically. Every repair and refinement is a proposal until the user accepts it.

The supported local harnesses are Codex CLI (`codex_cli`) and Claude Code
(`claude_cli`), alongside the existing API providers. The thesis comparison
deliberately focuses on GPT/OpenAI and Claude/Anthropic because compute and
budget resources are limited; Gemini CLI and Antigravity/agy are intentionally
out of scope. See [LLM integration](docs/llm-integration.md) for executable
paths, authentication, isolation, and version-pinning guidance.

## Architecture

```
frontend/          React + bpmn-js — diagram editor, validation panel, chat
backend/app/
  model/           canonical BPMN IR + swappable converters (XML, JSON, YAML, Mermaid)
  validation/      tier 1 deterministic rules, tier 2 Woflan adapter (no LLM dependency)
  llm/             provider-agnostic client, model routing, versioned prompts
  api/             FastAPI endpoints: /diagrams, /validate, /repair, /chat, /history
```

See [`docs/architecture.md`](docs/architecture.md) for a detailed walkthrough.

## Repository map and retention

| Path | Role | Retention policy |
|---|---|---|
| `backend/app/`, `frontend/src/` | Runtime application code | Active source; versioned |
| `backend/tests/`, `evaluation/tests/` | Automated verification | Active source; versioned |
| [`data/`](data/README.md) | Benchmark, ground truth, source-corpus metadata, and test fixtures | Versioned selectively; see the data map |
| `data/import_cases/`, `data/rule_cases/`, `data/test_cases/` | Import, deterministic-rule, and integration fixtures | Active test data; versioned |
| `data/pmo-dataset/` | Locally fetched source corpus | Local input cache; only provenance files are versioned |
| `evaluation/` | Dataset generation and audit tooling | Active research code; versioned |
| `experiments/specs/runs/` | Exact executed configurations and selections | Reproducibility evidence; versioned |
| `experiments/results/` | Canonical E1–E9 outputs, manifests, analyses, and reviewed derivatives | Reproducibility evidence; versioned; only per-trial resume checkpoints are ignored |
| `experiments/notebook/` | Analysis notebook and thesis figure generator | Reproducibility evidence; versioned |
| `workspaces/` | Interactive session snapshots | Local runtime state; ignored, but not treated as a disposable build cache |
| `.venv/`, `frontend/node_modules/`, `frontend/dist/`, Python and pytest caches | Installed or generated artifacts | Recreatable and ignored |

The detailed experiment layout and the exceptions for manually reviewed or
rescored outputs are documented in [`experiments/README.md`](experiments/README.md).

## Quick start

**Requirements:** Python 3.12+, Node 18+, a `.env` file (copy from `.env.example`).

```bash
# backend
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
PYTHONPATH=backend uvicorn app.main:app --reload   # -> http://localhost:8000

# frontend (separate terminal)
cd frontend && npm ci && npm run dev                # -> http://localhost:5173

# or full-stack via Docker
docker compose up --build
```

The backend exposes its OpenAPI UI at `http://localhost:8000/docs` and a health
check at `http://localhost:8000/health`. The frontend started with Vite uses
port 5173; the containerized frontend uses port 3000.

## Running tests

```bash
make test             # backend + frontend
make test-backend
make test-frontend
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
| [`docs/README.md`](docs/README.md) | Documentation map, ownership, and update rules |
| [`docs/development.md`](docs/development.md) | Local setup, configuration, tests, and API discovery |
| [`docs/architecture.md`](docs/architecture.md) | System design, module responsibilities, data flow |
| [`docs/validation-rules.md`](docs/validation-rules.md) | Tier 1 deterministic rules (R001–R008) |
| [`docs/formal-checkers.md`](docs/formal-checkers.md) | Tier 2 formal checkers and the shared issue shape |
| [`docs/llm-integration.md`](docs/llm-integration.md) | LLM client layer, model routing, prompt versioning |
| [`docs/converter-format.md`](docs/converter-format.md) | Diagram model, converter protocol, adding new formats |
| [`docs/repair-loop.md`](docs/repair-loop.md) | Repair modes, dispatcher, edit ops |
| [`docs/experiments.md`](docs/experiments.md) | `ExperimentConfig`, metrics, running a sweep |
| [`docs/run.md`](docs/run.md) | The `run` reproducibility envelope |
| [`evaluation/README.md`](evaluation/README.md) | Dataset construction and audit entry point |
| [`experiments/README.md`](experiments/README.md) | Executed E1–E9 runs and retained artifacts |

The repository does not currently declare a reusable software license. The
derived evaluation dataset has its own source attribution and license record in
[`data/eval/v1.0/ATTRIBUTION.md`](data/eval/v1.0/ATTRIBUTION.md).
