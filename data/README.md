# Data layout

This directory separates reproducible evaluation inputs from small development
fixtures and from the locally fetched source corpus.

| Path | Purpose | Versioned |
|---|---|---|
| `eval/v1.0/` | Final benchmark seeds, variants, descriptions, ground truth, and manifests | yes |
| `eval/enhancement/` | E8 requirement-driven refinement cases and audit evidence | yes |
| `import_cases/` | Parser and unsupported-input regression fixtures | yes |
| `rule_cases/` | Broken/fixed examples for Tier 1 rules | yes |
| `test_cases/` | Integration and demonstration fixtures, including the Chapter 6 walkthrough | yes |
| `pmo-dataset/` | Source corpus fetched from Zenodo | only provenance files |

The evaluation runner reads `eval/`; quantitative results must not be computed
from `import_cases/`, `rule_cases/`, or `test_cases/`. The source corpus remains
read-only. Build a derived dataset in a separate output directory and audit it
before replacing or versioning any snapshot.

See [`eval/README.md`](eval/README.md) for the benchmark schema,
[`../evaluation/METHODOLOGY.md`](../evaluation/METHODOLOGY.md) for construction
rules, and [`pmo-dataset/SOURCE.md`](pmo-dataset/SOURCE.md) for source retrieval.
