# Semantic defect variants

Semantic variants use the same `variants/`, `descriptions/variants/`, and
`ground_truth/` layout as structural and soundness variants.

- `single/M01` through `single/M07`: 199 human-verified single defects.
- `disjoint/Mxx+Myy`: 37 deterministic pairs with non-overlapping immediate
  control-flow footprints.
- `01-M06` was excluded because its candidate also produced Tier-1 `R007`.

The matching ground-truth JSON is the complete construction trace. It records
the exact ordered `injection` operations, inverse `repair`, expected semantic
finding, affected elements, and the description claim/relation used to verify
the error. Every published candidate passed BPMN round-trip, Tier 1, Woflan,
and inverse restoration. Pairs additionally passed order-commutativity and
component-preservation checks. No LLM output is needed to replay a mutation.

LLMs (`gpt-5.6-luna` and `gpt-5.6-terra`) assisted with proposing some
case-specific operations. Human verification established inclusion; the model
proposal itself was never treated as a semantic verdict.
