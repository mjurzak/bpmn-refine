# Experiment analysis protocol

This protocol separates facts that the benchmark can support from quantities
that still require human adjudication.

## Ground truth layers

`data/eval/v1.0/ground_truth/` remains the immutable mutation truth. The
versioned `ground_truth_overlay.json` adds two kinds of information without
rewriting that dataset:

1. a baseline status for every source seed; and
2. surviving localization references for defects that delete their original
   target element.

Source seeds are assumed to be mutation-free because they come from the PMO
source corpus, but they are not assumed to have exhaustive semantic-negative
annotations. Any model output on a source seed is therefore a `control alert`,
not an automatic false positive. A conventional clean-control false-positive
rate is reported only when an optional overlay says `status: clean` and
`human_verified: true`.

The overlay generator derives localization from the union of each mutation's
construction footprint and its semantic anchor elements that still exist in
the variant. This includes unchanged sibling elements needed to express a
relation, not only the directly mutated ID. If an entire path was deleted, it
uses surviving boundary elements from the inverse repair. These references are
construction metadata, not model-generated truth.

Rebuild the overlay deterministically:

```bash
source .venv/bin/activate
PYTHONPATH=backend:. python experiments/prepare_ground_truth_overlay.py \
  data/eval/v1.0 \
  --output data/eval/v1.0/ground_truth_overlay.json
```

## Reported semantic views

Analysis reports distinct views instead of conflating them:

- injected-target category recall: did the model emit the benchmark category?
- injected-target anchor recall: did a finding overlap the surviving injected
  footprint, regardless of the chosen category?
- category accuracy given anchor: conditional taxonomy accuracy after locating
  the correct defect;
- classification-basis coverage and category/basis consistency: whether the
  model identified the observable mismatch before assigning a category;
- localization coverage and overlap: how often references were supplied and
  how well they overlap the construction footprint;
- exact localization: a deliberately strict diagnostic, not the primary score;
- unadjudicated extra findings and source-control alert rate, neither treated
  as semantic false positives;
- verified-clean false-positive rate as an optional adjudicated diagnostic;
- latency, provider-normalized usage, errors, and paired bootstrap uncertainty.

Mutation truth is positive-unlabeled ground truth: it certifies injected targets
but does not certify that no other semantic issue exists. Primary analysis
therefore reports target recall without semantic precision. Extra findings are
`unadjudicated`; the historical precision/F1 view remains a conservative lower
bound in which every unmatched prediction is provisionally counted as false.
It is not used for the primary ranking.

Primary ranking uses target-anchor recall, category accuracy given an anchor,
machine-checkable response-contract completeness, and then comparable usage.
Control alert rate and unadjudicated extras are descriptive and do not change
quality rank. No quality winner is selected when provider errors occurred or
the paired target-anchor-recall confidence interval crosses zero.

The seven semantic labels are adjacent views of a violation, not seven natural
classes guaranteed to be mutually exclusive. In particular, a wrong label can
look like an absent activity, a missing final outcome can look like a missing
last step, and a forbidden branch action can also imply that the required
action is absent. The application therefore asks for an observable
`classification_basis` before the category. Exact-category accuracy remains a
taxonomy diagnostic; a correctly localized, evidence-backed relation must be
reported separately and must not be turned into a miss solely because an
adjacent label was selected.

## Optional human baseline adjudication

Human review is required only if the thesis claims exhaustive semantic
precision or a true false-positive rate. In that optional extension, use two
blinded reviewers and a tie-break reviewer. Findings need an explicit
description line plus BPMN element references. Record `clean`, `defect`, or
`ambiguous`; exclude ambiguous cases from conventional precision/FPR.

Model outputs may be shown to adjudicators only after their independent review.
Agreement between models is a candidate for review, not ground truth.

## Current rerun gate

The original 60 outputs remain valid for rescoring after analyzer or overlay
changes. Model calls are required only when the prompt or payload changes.

The cheap diagnostic in `experiments/specs/pilots/taxonomy-v3-luna.yaml` uses
GPT-5.6 Luna medium, three source controls, and one variant per semantic
operator. It is intentionally not a one-to-one model comparison. A full rerun
is justified only when:

- the diagnostic finishes without schema or transport errors;
- evidence fields and element references are valid;
- target-anchor detection and taxonomy are materially improved across the
  operator panel; and
- source-control alerts materially decrease without prompt overfitting.

Source-control outputs remain diagnostic and must not be called false positives
without optional exhaustive review.
