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

A seed is a clean control only when its overlay says `status: clean` and
`human_verified: true`. `unreviewed` and `ambiguous` controls do not contribute
to precision, false-positive rate, ranking, or a winner decision.

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

- injected category detection: did the model emit the benchmark category?
- defect-anchor detection: did a finding overlap the surviving injected
  footprint, regardless of the chosen category?
- category accuracy given anchor: conditional taxonomy accuracy after locating
  the correct defect;
- classification-basis coverage and category/basis consistency: whether the
  model identified the observable mismatch before assigning a category;
- localization coverage and overlap: how often references were supplied and
  how well they overlap the construction footprint;
- exact localization: a deliberately strict diagnostic, not the primary score;
- verified-clean false-positive rate: only over adjudicated clean controls;
- latency, provider-normalized usage, errors, and paired bootstrap uncertainty.

Until each variant inherits an adjudicated seed baseline, injected-category
precision/F1 are mutation-target diagnostics rather than whole-diagram semantic
precision/F1. Extra findings can be latent baseline defects. Recall and
anchor-localized taxonomy are the safer post-hoc quantities.

No quality winner is selected when clean controls are not verified, provider
errors occurred, or the paired confidence interval crosses zero.

The seven semantic labels are adjacent views of a violation, not seven natural
classes guaranteed to be mutually exclusive. In particular, a wrong label can
look like an absent activity, a missing final outcome can look like a missing
last step, and a forbidden branch action can also imply that the required
action is absent. The application therefore asks for an observable
`classification_basis` before the category. Exact-category accuracy remains a
taxonomy diagnostic; a correctly localized, evidence-backed relation must be
reported separately and must not be turned into a miss solely because an
adjacent label was selected.

## Human baseline adjudication

All 43 source seeds require review against the numbered reference description.
Use two blinded reviewers and a tie-break reviewer. Findings need an explicit
description line plus BPMN element references. Record `clean`, `defect`, or
`ambiguous`; exclude ambiguous cases from automatic precision and ranking.

Model outputs may be shown to adjudicators only after their independent review.
Agreement between models is a candidate for review, not ground truth.

## Prompt-v2 rerun gate

The original 60 outputs remain valid for rescoring after analyzer or overlay
changes. Model calls are required only when the prompt or payload changes.

Before rerunning all of E1, run `experiments/specs/pilots/prompt-v2.yaml`. The
pilot uses the same GPT-5.6 Terra medium and Claude Sonnet 5 default-thinking
configurations. It contains three seed candidates and one variant per semantic
operator. A full rerun is justified only when:

- both providers finish without schema or transport errors;
- evidence fields and element references are valid;
- M03 and M06 taxonomy improves, with broad improvement across M01-M07; and
- unsupported findings on all three seed candidates materially decrease.

The seed candidates remain unadjudicated, so their outputs are diagnostic and
must not be called false positives until review.
