# evaluation

The current quantitative evaluation matrix and execution constraints are in
[`EXPERIMENT_PLAN.md`](EXPERIMENT_PLAN.md). Dataset construction and ground
truth are specified separately in [`METHODOLOGY.md`](METHODOLOGY.md).

```
data/pmo-dataset/     Zenodo, gitignored, read-only
   -> generator       eligibility probe, seed gate, defect injection
data/eval/v1.0/       versioned benchmark dataset
   -> make experiment SPEC=experiments/specs/<x>.yaml OUT=experiments/results/<x>
experiments/results/<id>/results.jsonl
   -> metrics         tables and figures for thesis Chapter 6
```

The generator, dataset audit, experiment-spec generator, result analyzer, and
E8 runner are implemented. [`METHODOLOGY.md`](METHODOLOGY.md) specifies dataset
construction; [`docs/experiments.md`](../docs/experiments.md#metrics) specifies
the recorded metrics.

```bash
make eligibility-probe OUT=/tmp/bpmn-probe-v1.0   # which source models qualify as seeds
make dataset-build PROBE=data/eval/v1.0/probe.json OUT=/tmp/bpmn-eval-v1.0 VERSION=v1.0
make dataset-refresh-manifest DATASET=data/eval/v1.0  # only after reviewed curation
make dataset-audit DATASET=data/eval/v1.0    # hashes, joins, findings, repairs
```

The probe (`generator/probe.py`) is stage one: it applies the seed gate of [METHODOLOGY §2](METHODOLOGY.md#2-the-expected-finding-is-derived-from-the-operator) to every `.bpmn` in the source corpus and writes `probe.json`, `EXCLUSIONS.md` and `seeds.txt`. It calls no model provider, so it is free and repeatable — rerun it whenever parser coverage moves, since that is what decides who is a seed.

The build is stage two. It copies the 43 admitted source models and their descriptions, then emits 313 variants. The `k=1` stratum contains 227 instances: 42 for `S01`, 43 for `S02`, 43 for `S03`, 20 for `F01`, 33 for `F02`, 3 for `F03`, and 43 for `F04`. The two `k=2` strata add one reproducibly selected pair per seed: 43 disjoint and 43 interacting variants. Seed 36 has a message start event, which the current `AddNodeOp` cannot reconstruct, so `S01` is inapplicable there. `applicability.json` records every eligible and selected soundness injection site. The builder checks tier-1 findings, runs Woflan for the SOUND variants, and verifies each injection and inverse repair against its seed.

The `v1.0` snapshot additionally contains 198 human-verified single SEM
variants and 37 verified disjoint SEM+SEM variants. Their
construction trace is stored with the rest of the dataset; see
[`data/eval/v1.0/SEMANTIC.md`](../data/eval/v1.0/SEMANTIC.md). Intermediate
catalogues, prompts, and screening copies are intentionally not retained.

The E8 refinement slice is separate at `data/eval/enhancement/`. It contains
six M01 cases plus three deterministic cases for each of
M02, M03, M04, M06, and M07. Each case has a valid `M_core` diagram, the clean
reference, held-out verified anchor lines as `d_extra`, replayable atomic
oracle operations, and deterministic relation contracts. Build and audit it
without providers with `make enhancement-build` and
`make enhancement-audit TIER2=off`. Rehearse the real chat/refinement path
with `make e8-rehearse OUT=/tmp/e8-results.jsonl`.

**The join.** A trial record carries `input_path` and `input_hash` and nothing else about its input—the runner has no ground-truth concept. Metrics remove the `variants/` prefix and extension, then look up the same relative path under `ground_truth/`. For example, `variants/interacting/S03+S03/55.bpmn` joins `ground_truth/interacting/S03+S03/55.json`.
