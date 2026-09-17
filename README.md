# bpmn-refine

Interactive system for validation, repair, and refinement of BPMN 2.0 diagrams using Large Language Models.

Part of a master's thesis: *Design and Implementation of an Interactive System Supporting Validation, Repair, and Refinement of BPMN Diagrams with Large Language Models* — AGH University of Krakow.

## What it does

- **Validates** BPMN diagrams with deterministic structural rules (R001–R008), Petri-net soundness checking, and LLM semantic review
- **Repairs** diagrams by proposing targeted fixes for detected issues
- **Refines** diagrams through chat: the user describes intent, the system suggests changes

Repair and refinement proposals require acceptance by default. A user can explicitly enable Auto mode to apply a proposal and continue the repair loop.

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

## Quick start

Run all commands from the repository root. You need Python 3.12 or newer, `uv`, `make`, and Node.js 22.x (22.22.2 or newer) with `npm`.

Install the locked dependencies:

```bash
uv sync --extra dev --frozen
source .venv/bin/activate
npm --prefix frontend ci
```

Start the backend in one terminal:

```bash
make backend
```

Start the frontend in another terminal, again from the repository root:

```bash
make frontend
```

Open `http://localhost:5173`. The backend is available at `http://localhost:8000`, with OpenAPI at `http://localhost:8000/docs` and a health check at `http://localhost:8000/health`.

For a first check, import `data/rule_cases/R000_valid_baseline.bpmn` and click **Verify Rules**. The panel should show **No issues found**. This check needs no `.env` file or LLM provider. Semantic validation, chat, and LLM-based repair need a configured provider. Create the local configuration without overwriting an existing file, then replace the relevant placeholder with a valid credential:

```bash
test -e .env || cp .env.example .env
```

Restart the backend after changing `.env`. Use the **Models** control in the toolbar to choose the provider and model for each LLM interaction. Hosted providers require the matching API key. Ollama requires a local server at `OLLAMA_BASE_URL`; Codex CLI and Claude Code use existing local authentication when their executable is available.

Docker Compose requires a root `.env` file. Create it without overwriting an existing file, then run:

```bash
test -e .env || cp .env.example .env
docker compose up --build
```

The containerized frontend uses `http://localhost:3000`.

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

## Running tests

```bash
make test             # backend + frontend
make test-backend
make test-frontend
```

## CLI

Run the backend-local CLI without starting FastAPI:

```bash
PYTHONPATH=backend .venv/bin/python -m app.cli validate data/rule_cases/R000_valid_baseline.bpmn
PYTHONPATH=backend .venv/bin/python -m app.cli validate data/rule_cases/R000_valid_baseline.bpmn --json
PYTHONPATH=backend .venv/bin/python -m app.cli roundtrip data/rule_cases/R000_valid_baseline.bpmn --out /tmp/roundtrip.bpmn
PYTHONPATH=backend .venv/bin/python -m app.cli batch-validate data/rule_cases --recursive --format jsonl --out /tmp/validation.jsonl
```

With a configured LLM provider, chat and repair can use the same versioned fixture:

```bash
PYTHONPATH=backend .venv/bin/python -m app.cli chat data/rule_cases/R000_valid_baseline.bpmn --message "add a manager approval step" --out /tmp/refined.bpmn
PYTHONPATH=backend .venv/bin/python -m app.cli repair data/rule_cases/R000_valid_baseline.bpmn --out /tmp/repaired.bpmn
```

With `.venv` activated, the CLI is also exposed as:

```bash
bpmn-refine validate data/rule_cases/R000_valid_baseline.bpmn
```

Experiment-oriented commands support metadata such as runtime, dataset path, provider, model, and prompt filename:

```bash
bpmn-refine validate data/rule_cases/R000_valid_baseline.bpmn --json
bpmn-refine repair data/rule_cases/R000_valid_baseline.bpmn --json
bpmn-refine batch-validate data/rule_cases --recursive --format json --out /tmp/results.json
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

## License

The project's original code, including the backend and frontend, is licensed
under the GNU Affero General Public License version 3 only
([AGPL-3.0-only](LICENSE)). See [LICENSING.md](LICENSING.md) for scope and
third-party exceptions. The derived evaluation dataset retains its separate
[source attribution and license](data/eval/v1.0/ATTRIBUTION.md).
