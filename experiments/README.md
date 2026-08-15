# experiments

`specs/<ablation>.yaml` in, `results/<experiment_id>/` out. The config schema, the runner, and the record format are [`docs/experiments.md`](../docs/experiments.md).

```bash
make experiment-rehearse SPEC=experiments/specs/smoke.yaml OUT=/tmp/rehearsal   # mocked, free
make experiment SPEC=experiments/specs/benchmark/model-selection.yaml OUT=experiments/results/model-selection
make experiment SPEC=experiments/specs/benchmark/model-selection.yaml OUT=experiments/results/model-selection CONCURRENCY=2
```

Results stay in this repo, not in `research/`, so a sweep is a single-repository operation.
Each completed trial is atomically checkpointed under `results/<experiment_id>/trials/`.
`results.jsonl` is a deterministic compatibility export assembled from those files.
This makes bounded concurrent execution safe; do not run multiple processes against
the same output directory.

Minimal benchmark specs can be generated from the frozen evaluation dataset:

```bash
source .venv/bin/activate
PYTHONPATH=backend python experiments/prepare_experiments.py \
  --output-dir experiments/specs/benchmark \
  --gpt-model gpt-5.6-terra --claude-model claude-sonnet-5 \
  --winner-provider codex_cli --winner-model gpt-5.6-terra \
  --winner-pipeline full --semantic-panel-size 30
```

The checked-in cost-quality profile compares Claude Sonnet 5 with GPT 5.6
Terra. GPT uses medium reasoning and Claude Code keeps its default thinking.
This profile is fixed before the selection run;
Opus and Sol are outside the paid benchmark rather than silent fallbacks. The
30-case semantic panel is nested in the available 100-case panel, so expansion
reuses completed trial IDs. LLM-facing specs contain at most 100 inputs; the full
dataset remains available for provider-free Tier 1 and Tier 2 evaluation.

The checked-in `specs/benchmark/` files are the executable benchmark specs.
Run `model-selection.yaml` first and analyze it. Generate the files again with
the winning provider and model before IR selection and the small ablations.
Generate them once more with the E5 pipeline winner. Run confirmatory detection
only after that configuration is frozen. Each selection stage supplies
configuration values to the stages that follow it.
All generated detection files use `run_repair: false`.

```bash
python experiments/analyze_results.py <results.jsonl> data/eval/v1.0 \
  --output <analysis.json>
```

Provider values are `openai`, `anthropic`, `ollama`, `gemini`, `codex_cli`, and
`claude_cli`. The thesis comparison deliberately focuses on GPT/OpenAI and
Claude/Anthropic because compute and budget resources are limited. For CLI sweeps, install and pin the
Codex/Claude Code versions, configure their executable paths in `.env`, and
record the CLI versions with the results; no live-model calls are made by the
rehearsal target.

When the benchmark is frozen, each semantic sweep also declares the mirrored
description tree. The runner refuses to start if any variant lacks its matching text.

```yaml
dataset_version: v1.0
inputs:
  - data/eval/v1.0/variants/**/*.bpmn
description_root: data/eval/v1.0/descriptions
```

`smoke.yaml` points at test fixtures so `make experiment-rehearse` always has a
cheap, provider-free target. It is not a benchmark spec.
