# Experiments

This directory contains the executed E1–E9 specifications, stored results, and one executed notebook for reviewing the analyses and recreating thesis Chapter 6 figures.

## Layout

```text
specs/runs/   exact executed configurations and selections
results/      canonical run records and analyses
notebook/     main analysis view and thesis figure generator
```

The results directory is versioned. Only resumable trials checkpoints and temporary files are ignored.

## Runs

| Study | Input specification | Command | Result |
|---|---|---|---|
| E1 model | `specs/runs/e1-model-selection.yaml` | generic sweep | `results/e1-model-selection/` |
| E2 IR | `specs/runs/e2-ir-screening.yaml`, `e2-ir-holdout.yaml` | generic sweep | `results/e2-ir-*` |
| E3 reasoning | `specs/runs/e3-reasoning-terra.yaml`, `e3-reasoning-luna.yaml` | generic sweep | `results/e3-reasoning-*` |
| E4 description | `specs/runs/e4-description.yaml` | generic sweep | `results/e4-description/` |
| E5 validators | `specs/runs/e5-validator-screening.yaml`, `e5-validator-holdout.yaml` | generic sweep | `results/e5-validator-*` |
| E6 detection | `specs/runs/e6-confirmatory-detection.yaml` | generic sweep | `results/e6-detection/` |
| E7 repair | `specs/runs/e7-repair-selection.json` | `run_e7.py` | `results/e7-repair/` |
| E7b diagnostic | `specs/runs/e7b-safe-loop-diagnostic.json` | `run_e7.py` | `results/e7b-repair-safe-loop/` |
| E8 refinement | `specs/runs/e8-refinement.json`, `../data/eval/enhancement/cases.json` | `run_e8.py` | `results/e8-refinement/` |
| E9 repeatability | `specs/runs/e9-repeatability.yaml`, frozen E6 records | generic sweep + `analyze_e9.py` | `results/e9-repeatability/` |

E2–E5 use frozen, paired selection panels. E6 is the confirmatory 100-case detection run. E7 contains 13 eligible repair cases, E8 all 21 refinement cases, and E9 three observations of ten fixed E6 cases. Exact model, prompt, dataset, harness, configuration, reuse, and manual-review provenance is stored in each spec and result directory.

## Reproduction

Run commands from the repository root:

```bash
# free path/configuration rehearsal
make experiment-rehearse \
  SPEC=experiments/specs/runs/smoke.yaml \
  OUT=/tmp/bpmn-experiment-rehearsal

# live E1 execution; this sends provider requests
make experiment \
  SPEC=experiments/specs/runs/e1-model-selection.yaml \
  OUT=experiments/results/e1-model-selection \
  CONCURRENCY=4

# provider-free E6 analysis example
PYTHONPATH=backend:. .venv/bin/python experiments/analyze_e6.py \
  experiments/results/e6-detection/results.jsonl \
  data/eval/v1.0 \
  --output /tmp/e6-analysis.json
```

The executed notebook is the user-facing entry point. E1–E6 use the generic sweep runner. E7 and E8 keep separate internal drivers because they exercise the repair loop and chat refinement path. Small analysis modules remain separate so the notebook can reuse their metrics; they never call a provider:

- `analyze_results.py` contains shared detection metrics.
- `analyze_e5.py`, `analyze_e6.py`, and `analyze_e9.py` add study-specific views.
- `run_e7.py analyze` and `run_e8.py --analyze` analyze their own outputs.
- `rescore_e8.py` reapplies deterministic E8 scoring after manual review.
- `prepare_ground_truth_overlay.py` rebuilds localization anchors used by the
  common analyzer; it is dataset preparation, not an experiment runner.

The final repair and refinement analyses can be reproduced directly without a
provider call:

```bash
PYTHONPATH=backend:. .venv/bin/python experiments/run_e7.py analyze \
  experiments/results/e7-repair/results.jsonl \
  experiments/specs/runs/e7-repair-selection.json

PYTHONPATH=backend:. .venv/bin/python experiments/run_e8.py \
  data/eval/enhancement/cases.json \
  --out experiments/results/e8-refinement/results-rescored.jsonl \
  --analyze
```

E8 automatically includes the sibling `manual-review.json` summary when that
file is present. The review decisions themselves are versioned inputs and are
never inferred again by the analyzer.

The primary measures are injected-target category recall, expected-element
overlap, and their conjunction. Clean structural and formal controls can be
scored as false positives. Semantic mutation truth is positive-unlabeled: it
certifies the injected target but does not prove that no other semantic issue
exists. Unmatched semantic findings and source-control findings therefore remain
unadjudicated alerts unless a human-reviewed baseline says otherwise. Repair and
refinement additionally report target removal, post-validation safety,
preservation, and explicit manual review where deterministic checks are
insufficient.

## Artifacts

A completed run keeps raw `results.jsonl`, archival `run.json`, and the analyses
required by the notebook. During execution, the generic runner also maintains a
`manifest.json` and resumable `trials/` checkpoints. The curated archival
`run.json` embeds the relevant manifest and adds integrity or provenance notes;
the E9 directory retains its standalone manifest because its analysis consumes
it directly. Combined or rescored JSONL files remain only when they record reused
observations or reviewed decisions. Trial checkpoints are disposable after the
deterministic `results.jsonl` export is complete.

The executed notebook at
[`all-experiments-results.ipynb`](all-experiments-results.ipynb)
reads these stored artifacts, shows the main tables and all seven figures, and
writes the PDF copies referenced by the thesis to the sibling
`thesis/figures/ch06/` directory. It makes no model calls. Run it only when an
intentional cross-repository figure refresh is desired; a normal application or
dataset test does not modify the thesis repository.
