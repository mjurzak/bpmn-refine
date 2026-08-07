# evaluation

```
data/pmo-dataset/     Zenodo, gitignored, read-only
   -> generator       eligibility probe, seed gate, defect injection
data/eval/v1.0.0/     the benchmark, committed
   -> make experiment SPEC=experiments/specs/<x>.yaml OUT=experiments/results/<x>
experiments/results/<id>/results.jsonl
   -> metrics         tables and figures for thesis Chapter 6
```

`generator/` is in progress and `metrics/` is not written yet. [`METHODOLOGY.md`](METHODOLOGY.md) specifies the first, [`docs/experiments.md`](../docs/experiments.md#metrics) the second.

```bash
make eligibility-probe OUT=data/eval/v1.0.0   # which source models qualify as seeds
```

The probe (`generator/probe.py`) is stage one: it applies the seed gate of [METHODOLOGY §2](METHODOLOGY.md#2-the-expected-finding-is-derived-from-the-operator) to every `.bpmn` in the source corpus and writes `probe.json`, `EXCLUSIONS.md` and `seeds.txt`. It calls no model provider, so it is free and repeatable — rerun it whenever parser coverage moves, since that is what decides who is a seed.

**The join.** A trial record carries `input_path` and `input_hash` and nothing else about its input - the runner has no ground-truth concept. Metrics recover the expected outcome from the input path's stem, so `variants/<vid>.bpmn` and `ground_truth/<vid>.json` must keep matching stems. Rename one side and the join returns empty instead of failing.
