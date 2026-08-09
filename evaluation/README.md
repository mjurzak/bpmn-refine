# evaluation

```
data/pmo-dataset/     Zenodo, gitignored, read-only
   -> generator       eligibility probe, seed gate, defect injection
data/eval/v1.3.0/     current generated snapshot
   -> make experiment SPEC=experiments/specs/<x>.yaml OUT=experiments/results/<x>
experiments/results/<id>/results.jsonl
   -> metrics         tables and figures for thesis Chapter 6
```

`generator/` is in progress and `metrics/` is not written yet. [`METHODOLOGY.md`](METHODOLOGY.md) specifies the first, [`docs/experiments.md`](../docs/experiments.md#metrics) the second.

```bash
make eligibility-probe OUT=/tmp/bpmn-probe-v1.3.0   # which source models qualify as seeds
make dataset-build PROBE=data/eval/v1.3.0/probe.json OUT=/tmp/bpmn-eval-v1.3.0 VERSION=v1.3.0
make dataset-audit DATASET=data/eval/v1.3.0    # hashes, joins, findings, repairs
```

The probe (`generator/probe.py`) is stage one: it applies the seed gate of [METHODOLOGY §2](METHODOLOGY.md#2-the-expected-finding-is-derived-from-the-operator) to every `.bpmn` in the source corpus and writes `probe.json`, `EXCLUSIONS.md` and `seeds.txt`. It calls no model provider, so it is free and repeatable — rerun it whenever parser coverage moves, since that is what decides who is a seed.

The build is stage two. It copies the 43 admitted source models and their descriptions, then emits 184 single-defect variants: 42 for `S01`, 43 for `S02`, 20 for `F01`, 33 for `F02`, 3 for `F03`, and 43 for `F04`. Seed 36 has a message start event, which the current `AddNodeOp` cannot reconstruct, so `S01` is inapplicable there. `applicability.json` records every eligible and selected injection site. The builder checks tier-1 findings, runs Woflan for the SOUND variants, and verifies each injection and inverse repair against its seed.

## Visual inspection

Run the backend and frontend, then open `http://localhost:5173/dataset-compare`. The left selector chooses an admitted seed. The right selector lists only variants whose ground-truth record names that seed.

The page renders the original and variant side by side. Red marks elements removed from the variant, green marks additions, and amber marks elements whose BPMN properties changed. The comparison ignores BPMNDI geometry and derived `incoming`/`outgoing` children, so layout serialization does not appear as a process change.

**The join.** A trial record carries `input_path` and `input_hash` and nothing else about its input - the runner has no ground-truth concept. Metrics recover the expected outcome from the input path's stem, so `variants/<vid>.bpmn` and `ground_truth/<vid>.json` must keep matching stems. Rename one side and the join returns empty instead of failing.
