# Development guide

Run all commands from the repository root. The Makefile and Python packaging
configuration assume that working directory.

## Requirements

- Python 3.12 or newer;
- `uv` for the Python environment;
- Node.js 18 or newer with `npm`;
- Docker with Compose, only if the container path is used.

## Local setup

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env
npm --prefix frontend ci
```

Keep `.env` local. API keys are needed only for their respective hosted
providers. Codex CLI and Claude Code use their existing local authentication;
their adapters register only when the configured executable is available.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `LLM_PROVIDER` | Default provider | `openai` |
| `LLM_STRONG_PROVIDER` | Optional provider override for reasoning-critical work | falls back to `LLM_PROVIDER` |
| `LLM_FAST_PROVIDER` | Optional provider override for mechanical work | falls back to `LLM_PROVIDER` |
| `LLM_STRONG_MODEL` | Strong-tier model | `gpt-5.6-sol` |
| `LLM_FAST_MODEL` | Fast-tier model | `gpt-5.6-luna` |
| `CODEX_CLI_PATH` | Codex CLI executable | `codex` |
| `CLAUDE_CLI_PATH` | Claude Code executable | `claude` |
| `LLM_CLI_TIMEOUT_SECONDS` | Local CLI request timeout | `300` |
| `CORS_ORIGINS` | JSON list of allowed browser origins | local frontend ports |
| `OLLAMA_BASE_URL` | Ollama server | `http://localhost:11434` |
| `DIAGRAM_CONVERTER` | Registered canonical converter | `pydantic` |
| `WORKSPACE_DIR` | Interactive revision storage | `workspaces` |
| `API_PREFIX` | Prefix for application API routes | `/api/v1` |

Hosted providers additionally read `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or
`GEMINI_API_KEY`. The complete settings model is
`backend/app/core/config.py`; `.env.example` is the copyable template.

## Run the application

Use separate terminals:

```bash
make backend
make frontend
```

The backend is available at `http://localhost:8000`; the Vite frontend is at
`http://localhost:5173`. OpenAPI documentation is generated from the route and
Pydantic models at `http://localhost:8000/docs`.

For containers:

```bash
docker compose up --build
```

The containerized frontend is available at `http://localhost:3000`.
The backend image includes the versioned `data/eval/` snapshot required by the
read-only dataset comparison pages. It does not include the local PMo source
corpus or stored experiment results.

## Verification

```bash
make test             # all Python and frontend tests
make test-backend     # backend/tests and evaluation/tests
make test-frontend    # Vitest suite
make docs-check       # local Markdown links and documentation index
git diff --check      # whitespace and conflict-marker check
```

Provider-free experiment checks are available separately:

```bash
make dry-run OUT=/tmp/bpmn-dry-run
make experiment-rehearse \
  SPEC=experiments/specs/runs/smoke.yaml \
  OUT=/tmp/bpmn-experiment-rehearsal
make dataset-audit DATASET=data/eval/v1.0
make enhancement-audit TIER2=off
```

These commands do not authorize a live provider call. Use `make experiment`
only with an intentionally selected spec and output directory.

## Generated and local state

- `workspaces/` contains interactive session snapshots and is ignored.
- `data/pmo-dataset/` is a local source-corpus cache; only `INFO.md` and
  `SOURCE.md` are versioned.
- `.venv/`, `frontend/node_modules/`, frontend builds, and Python caches are
  recreatable and ignored.
- `experiments/results/*/trials/` contains resumable checkpoints. Canonical
  result exports, run records, analyses, and reviewed derivatives are versioned.

See [`../experiments/README.md`](../experiments/README.md) before changing any
stored experimental artifact.
