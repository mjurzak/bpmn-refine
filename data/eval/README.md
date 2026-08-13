# data/eval

Built from `data/pmo-dataset/` per [`evaluation/METHODOLOGY.md`](../../evaluation/METHODOLOGY.md). `v1.0/` is the single evaluation dataset: deterministic STRUCT/SOUND generation and final human-verified SEM curation are stored together. Later changes require a new dataset version rather than hidden replacement.

```
v1.0/
  manifest.json          version, source snapshot, RNG seed, counts, hashes,
                         assisting model family and construction trace
  seeds/<seed>.bpmn      models that passed the eligibility gate
  descriptions/seeds/    source descriptions for eligible seeds
  variants/<regime>/<operators>/<seed>.bpmn
  descriptions/variants/<regime>/<operators>/<seed>.txt
  ground_truth/<regime>/<operators>/<seed>.json
  applicability.json    eligible and RNG-selected F01-F04 soundness sites
  SEMANTIC.md            concise semantic construction and verification note
  EXCLUSIONS.md
```

**Relative paths must match.** The same identifier is used under `variants/`, `ground_truth/`, and `descriptions/variants/`. It has the form `<regime>/<operators>/<seed>`, for example `single/F02/07` or `interacting/S03+S03/55`. Injection sites belong in ground truth, never in filenames.

```json
{
  "variant_id": "single/F02/07",
  "seed": "07",
  "operators": ["F02"],
  "class": "SOUND",
  "expected_finding": "lack_of_synchronisation",
  "expected_elements": ["Gateway_1a2b3c"],
  "expected_findings": ["lack_of_synchronisation"],
  "multiplicity": 1,
  "interaction": "single",
  "defects": [ { "operator": "F02", "expected_finding": "lack_of_synchronisation", "expected_elements": ["Gateway_1a2b3c"], "injection_site": ["Gateway_1a2b3c"] } ],
  "injection": [ { "op": "change_gateway_type", "id": "Gateway_1a2b3c", "new_type": "exclusiveGateway" } ],
  "repair": [ { "op": "…" } ],
  "detected_by_construction": true,
  "detected_by": { "tier1": [], "woflan": ["woflan:soundness"] },
  "anchor": null
}
```

`injection` stores the exact mutations applied to the seed; `S03` uses the generator-local `repoint_flow` operation, while the other operators use production `EditOp`s. `repair` always stores a production `EditOp` plan. `defects` keeps each expectation separate for `k=2`; the singular fields summarize the complete record. `interaction` is `single`, `disjoint`, or `interacting`. Expected findings come from operator definitions, not from running the checker under evaluation. SEM records additionally store `human_verified`, technical gates, and an `anchor` containing the description claim and relation.

`ATTRIBUTION.md` identifies the PMo source, its DOI and CC BY 4.0 license, and the changes made by the generator. `manifest.json` records SHA-256 hashes for every generated or copied file except the manifest itself.

Sibling directories — `rule_cases/`, `test_cases/`, `import_cases/` — are unit-test fixtures, and `.cases/` is the Chapter 6 case study. No thesis number comes from any of them.
