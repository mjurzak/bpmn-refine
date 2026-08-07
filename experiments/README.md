# experiments

`specs/<ablation>.yaml` in, `results/<experiment_id>/` out. The config schema, the runner, and the record format are [`docs/experiments.md`](../docs/experiments.md).

```bash
make experiment-rehearse SPEC=experiments/specs/smoke.yaml OUT=/tmp/rehearsal   # mocked, free
make experiment SPEC=experiments/specs/ir_format.yaml OUT=experiments/results/ir-format
```

Results stay in this repo, not in `research/`, so a sweep is a single-repository operation.

**The four ablation specs resolve to `data/test_cases/` and `data/rule_cases/` - eleven unit-test fixtures. No thesis number may come from them.** They point there because `data/eval/v1.0.0/` does not exist yet, and a spec that cannot resolve its inputs is one nobody can rehearse. Each declares `dataset_version: dev-fixtures`, which the manifest records, so a fixture run cannot later pass for a benchmark run.

When the generator has emitted the dataset, each spec changes in exactly two places:

```yaml
dataset_version: v1.0.0
inputs:
  - data/eval/v1.0.0/variants/*.bpmn
```

`smoke.yaml` keeps pointing at the fixtures afterwards — it is not an ablation, it exists so `test_the_shipped_specs_load_and_expand` and `make experiment-rehearse` always have a cheap target.
