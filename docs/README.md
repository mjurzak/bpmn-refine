# Documentation map

This directory documents the delivered application. Dataset construction and
executed experiments have their own entry points because they are reproducible
research artifacts, not runtime subsystems.

## Start here

| Need | Authoritative document |
|---|---|
| Install, configure, run, or test the project | [`development.md`](development.md) |
| Understand components and request flows | [`architecture.md`](architecture.md) |
| Understand the canonical BPMN model and supported subset | [`converter-format.md`](converter-format.md) |
| Understand deterministic validation | [`validation-rules.md`](validation-rules.md) |
| Understand Woflan integration and limits | [`formal-checkers.md`](formal-checkers.md) |
| Understand providers, routing, prompts, and structured output | [`llm-integration.md`](llm-integration.md) |
| Understand repair modes and safety policy | [`repair-loop.md`](repair-loop.md) |
| Understand request configuration and the generic sweep runner | [`experiments.md`](experiments.md) |
| Understand response provenance | [`run.md`](run.md) |
| Build or audit the evaluation dataset | [`../evaluation/README.md`](../evaluation/README.md) |
| Inspect or reproduce E1–E9 | [`../experiments/README.md`](../experiments/README.md) |
| Inspect the stored benchmark format | [`../data/eval/README.md`](../data/eval/README.md) |
| Understand all data and fixture directories | [`../data/README.md`](../data/README.md) |

## Ownership boundaries

- `docs/` describes current application behavior and stable contracts.
- `evaluation/` defines how benchmark and refinement cases are constructed and
  audited.
- `data/eval/` stores the versioned inputs, ground truth, provenance, and
  manifests produced by that methodology.
- `experiments/specs/runs/` stores the exact executed selections and
  configurations.
- `experiments/results/` stores immutable raw observations and derived analyses.
- `experiments/notebook/` is the human-facing analysis view and figure generator.

Avoid copying experiment counts into architecture documents. Counts belong in
the versioned dataset manifest, run records, and the experiment map. Avoid
copying API schemas by hand: while the backend is running, FastAPI serves the
current OpenAPI specification at `/openapi.json` and its interactive UI at
`/docs`.

## Update rules

- Change a subsystem document in the same commit as the behavior it describes.
- Update `ExperimentConfig` examples when `backend/app/experiments.py` changes.
- Preserve executed specs and raw result files. Derived analyses may be
  regenerated only by their recorded analysis scripts.
- Do not overwrite `data/eval/v1.0/` after a methodological change; create a new
  dataset version.
- Keep local credentials, source-corpus caches, virtual environments, Node
  dependencies, and interactive session snapshots out of Git.
