# Defect Injection Methodology

The specification for `data/eval/v1.0.0/`, whose on-disk layout is [`data/eval/README.md`](../data/eval/README.md).

Scope: dataset `v1.0.0`, built from the PMo Dataset only, English descriptions. The eligibility probe (`evaluation/generator/probe.py`) admits 45 of the 55 PMo models as seeds; the 10 exclusions and their reasons are in [`data/eval/v1.0.0/EXCLUSIONS.md`](../data/eval/v1.0.0/EXCLUSIONS.md), regenerated with the probe rather than maintained by hand. Every seed carries an English description, so no operator class is unavailable on part of the corpus.

Vocabulary: a *seed* is a clean source model, a *defect operator* is a rule for introducing one fault, and a *variant* is a seed with one or more injected defects.

The catalogue and the generated dataset are frozen before any prompt work, and dataset versions are recorded separately from prompt versions in every run block.

## 1. Operators must be grounded outside the detector

An operator defined as the inverse of one of my own rules measures whether that rule detects its own negation. Every operator therefore cites a source independent of this implementation, and the [operator catalogue](#3-operator-catalogue) records that source per operator.

Three classes, with decreasing strength of claim:

- `STRUCT` violates the BPMN 2.0.2 metamodel. Tier 1 detects it by design, so I report it as calibration rather than as evidence.
- `SOUND` violates workflow-net soundness. These operators are the ones the counterexample ablation rests on.
- `SEM` contradicts the process description. The reference is natural language, so the [semantic layer protocol](#6-semantic-layer-protocol) covers it separately.

I do not claim these defects reproduce the frequency distribution of errors made by human modellers. That claim needs an observational corpus, which `v1.0.0` does not include.

## 2. The expected finding is derived from the operator

Woflan is tier 2 of the system under evaluation, so it cannot also decide whether a variant is faulty. Each operator instead carries an applicability condition and the finding that follows from its definition. Applying `F02` to a sound, well-formed seed produces lack of synchronisation by the Sadiq and Orlowska result, whether or not any checker runs. The generator writes that finding into the ground-truth record at construction time.

Determinism of the checker does not remove the need for this. Even where Woflan returns the same verdict for the same input, that says nothing about whether the verdict is correct. The recorded expectation is what makes a tier-2 false negative observable: without it, a miss and a genuine SOUND verdict produce the same output and cannot be told apart. One such divergence is already known, since pm4py encodes uncontrolled splits as free choice and Woflan then reports SOUND on a model with contradictory end states.

Seeds must be clean before injection: no tier-1 findings, and SOUND under tier 2. Two further conditions come before those, because a model that fails them cannot be injected into at all: the importer must keep every child of the source file, and the IR must survive its own export unchanged apart from layout the source never carried. Models failing any of the four move to `wild/` with the failing stage recorded, and are never used as seeds.

A fifth outcome is neither pass nor fail: tier 2 can return no verdict, by timing out or by crashing inside the checker. That leaves soundness unknown rather than false, so those models are excluded under their own reason and not counted as unsound.

## 3. Operator catalogue

| id | class | operation | grounding | expected finding |
|---|---|---|---|---|
| `S01` | STRUCT | delete the only start event | BPMN 2.0.2 | `R001` |
| `S02` | STRUCT | delete the only end event | BPMN 2.0.2 | `R002` |
| `S03` | STRUCT | repoint a sequence flow at an absent element id | BPMN 2.0.2 | `R006` |
| `S04` | STRUCT | delete the flow that connects a region to the start event | BPMN 2.0.2 | `R007` |
| `F01` | SOUND | close an XOR split with an AND join | Sadiq and Orlowska (2000) | deadlock |
| `F02` | SOUND | close an AND split with an XOR join | Sadiq and Orlowska (2000) | lack of synchronisation |
| `F03` | SOUND | delete a merging gateway, leaving concurrent paths at the end event | van der Aalst (1997) | improper completion |
| `M01` | SEM | delete a task realising a described action | [protocol](#6-semantic-layer-protocol) | `missing_step` |
| `M02` | SEM | swap two tasks whose order the text fixes | [protocol](#6-semantic-layer-protocol) | `contradictory_flow` |
| `M03` | SEM | negate a gateway condition stated in the text | [protocol](#6-semantic-layer-protocol) | `unreachable_branch` |
| `M04` | SEM | delete a described exception path | [protocol](#6-semantic-layer-protocol) | `missing_exception_handling` |
| `M05` | SEM | rename a task to a term the text uses for another object | [protocol](#6-semantic-layer-protocol) | `inconsistent_naming` |
| `M06` | SEM | make a described end state unreachable | [protocol](#6-semantic-layer-protocol) | `improper_termination` |

Which elements a finding must point at depends on the injection site, so that part of the ground truth lives in the per-instance record.

The `STRUCT` grounding column still needs the exact BPMN 2.0.2 clause per row, read off the specification PDF before this table is cited in the thesis.

Operators act on the IR, not on XML, so an injected defect is an `EditOp` and its ground-truth repair is the inverse `EditOp`. Defects outside the IR vocabulary (duplicate `xsd:ID`, malformed XML) belong to a separate `malformed/` set that exercises the import guard.

Semantic categories match `app/validation/rules.py:SemanticCategory`. If that enum changes, this table and the dataset version change with it.

## 4. Variants that leave the verdict unchanged

A variant that is still sound and still consistent with its description is equivalent to its seed. Discard it and record the operator and site that produced it.

A variant that is genuinely faulty but that none of the three tiers report stays in the dataset, labelled `undetected_by_construction`. These instances measure the detector's ceiling. Removing them would inflate recall by selecting for what the system already finds.

## 5. Site selection and defect multiplicity

Sites are drawn from the eligible positions for the operator using a recorded RNG seed, capped at two variants per seed model per class. Without the cap, a class can concentrate on a few models and the result reports a model effect.

Three multiplicity regimes, populated by design rather than by sampling:

- `k=1`, one defect in isolation, for per-class detection.
- `k=2` in disjoint subgraphs, for whether repair handles several findings at once.
- `k=2` sharing an element or region, for interaction between repairs.

The third regime is the only one under which the `max_repair_iters` sweep can separate its budgets, so it is not a marginal stratum.

## 6. Semantic layer protocol

A description underspecifies its model: the model may hold detail the text omits, and the reverse. A semantic defect must therefore contradict a statement the text makes explicitly, and the record must point at that statement. PMo descriptions carry one sentence per line, so a line index identifies it.

The asserted relation is drawn from a closed vocabulary, which lets both sides of the contradiction be checked on the IR:

| operator | relation | automatic check |
|---|---|---|
| `M01` | `exists_task(label)` | holds in the seed, fails in the variant |
| `M02` | `precedes(a, b)` | holds in the seed, reversed in the variant |
| `M03` | `branch_condition(gateway, condition)` | present in the seed, negated in the variant |
| `M04` | `exists_path(from, event)` | present in the seed, absent in the variant |
| `M05` | `distinct_labels(a, b)` | distinct in the seed, colliding in the variant |
| `M06` | `reaches(end_state)` | reachable in the seed, unreachable in the variant |

Renaming to a synonym is not a defect, so `M05` applies only when the substituted term names a different object elsewhere in the description.

Each `SEM` record adds:

```json
"anchor": {
  "description_line": 7,
  "relation": "precedes('Check invoice', 'Approve payment')",
  "violated_by": "M02"
}
```

The generator checks three things without human input: 
- the relation holds in the seed, 
- it fails in the variant, 
- the variant still passes tiers 1 and 2. 

A variant failing the third check measures tier 1 rather than tier 3, so it is either repaired structurally (reconnect the flow) or discarded.

Cited description line is always human-verified against the actual relation. Relations are shared across variants of the same seed, so the confirmations number in the dozens rather than the hundreds. [LLM assistance during construction](#8-llm-assistance-during-construction) covers how a model may assist with this step.

## 7. Refinement instances

Defect injection cannot evaluate refinement, because a refinement request carries no defect and produces no finding. Refinement instances are built by splitting the description instead.

Given seed `M` with description `D`, hold out one or two sentences as `D_extra` and keep the rest as `D_core`. Delete the elements realising `D_extra` from `M`, producing `M_core`. The task is then: given `M_core` and the instruction `D_extra`, extend the model. Ground truth is `M` itself.

`M_core` must be sound and free of tier-1 findings, so that the input is a correct model. It must also carry no residue of the deletion: no orphaned labels, no gateway left with one branch where `M` had two, no condition referring to a removed branch. These residue checks run on the IR.

Under `M01` the variant contradicts its full description and the system has to discover that through validation. Here `M_core` is consistent with `D_core` and the instruction arrives explicitly through the chat path, which makes the two separate tasks rather than one relabelled.

Scoring adds one measure the repair experiments lack: preservation of what was not requested. Report the share of `M_core` elements surviving unchanged, label stability, and soundness after the edit, alongside `GED(result, M)`. A refinement that adds the right task while rewriting half the model is a poor refinement.

## 8. LLM assistance during construction

A language model may propose the `D_core` / `D_extra` split and identify which elements realise `D_extra`, and may propose the anchor relations of the [semantic layer protocol](#6-semantic-layer-protocol). These are proposals into a human decision, and none of them touches the ground truth, which remains the original PMo model.

The deletion itself runs as code rather than as a model call. Once the element set is confirmed, removal is the inverse `EditOp` already used by `M01`, so it is deterministic and leaves no model-specific residue in `M_core`.

Preferably the assisting model is not one of the models under evaluation. Where that is impractical, the residual exposure is that the split favours sentences easy for models of that family, since the human confirms the split and the deletion is deterministic, so no model-specific artefact reaches `M_core`. Record the assisting model in the manifest with its version, prompt version, and date either way.

Report the rejection rate: how many proposals were made and how many I rejected. That figure is what separates verification from rubber-stamping.

## 9. Reporting

Results are reported per defect class. An aggregate over the classes is a weighted average whose weights are the class proportions in the dataset, and I fixed those proportions when composing it. Generating twice as many `STRUCT` instances would raise the aggregate figure without any change to the system. The aggregate therefore describes the dataset composition and not the detector.

## References

- van der Aalst, W.M.P. (1997). Verification of workflow nets.
- Sadiq, W. and Orlowska, M.E. (2000). Analyzing process models using graph reduction techniques.
- OMG (2014). BPMN 2.0.2 specification.
- Bellan, P. et al. (2022). PET: an annotated dataset for process extraction from text.
- Brissard, A., Cuppens, F. and Zouaq, A. (2025). What is the best process model representation?
