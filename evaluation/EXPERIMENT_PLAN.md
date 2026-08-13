# Experiment plan

This file defines the quantitative experiments. See
[`METHODOLOGY.md`](METHODOLOGY.md) for dataset construction. See
[`docs/experiments.md`](../docs/experiments.md) for runner details.

## Common rules

The experiments use Codex CLI and Claude Code. They use the researcher's
existing subscriptions. The measured unit is a model and its CLI harness.

Apply these rules to each evaluated request:

- Start a new single-turn session.
- Use the same versioned prompt and response schema.
- Disable memory, repository instructions, and tools where possible.
- Record the model, harness, harness version, prompt, dataset, and settings.
- Record errors, quota stops, latency, and reported usage.
- Do not count a quota stop as a model error.
- Keep paid overage disabled.

Use all eligible source seeds in each applicable experiment. E3 and E4 use one
fixed diagnostic sample because they sweep multiple settings. Freeze generated
specs before the first evaluated request.

## Defect groups

- `single`: The diagram contains one injected defect.
- `disjoint`: The diagram contains two defects in separate regions.
- `interacting`: The diagram contains two defects in one shared region.
- `clean`: The source diagram contains no injected defect.

Semantic defects include missing steps, contradictory flows, unreachable
branches, missing exception paths, naming conflicts, bad termination, and
unwanted actions.

Structural defects include missing boundary events, invalid references, and
unreachable regions. Formal defects include deadlocks, synchronization errors,
improper completion, and dead transitions.

## Experiment summary

| ID | Experiment | Data | Result |
|---:|---|---|---|
| E1 | Model comparison | Single semantic defects and clean controls | Selected model and harness |
| E2 | IR comparison | Single semantic defects and clean controls | Selected IR |
| E3 | Reasoning ablation | Small single-defect sample and clean controls | Selected reasoning level |
| E4 | Description ablation | The E3 sample | Measured value of the reference description |
| E5 | Validator ablation | Single structural and formal defects and clean controls | Measured value of deterministic validators |
| E6 | Detection benchmark | Single, disjoint, interacting, and clean cases | Final detection results |
| E7 | Repair experiment | Eligible single and paired cases | Final repair results |
| E8 | Enhancement/refinement | 21 deterministic semantic `M_core` cases | Refinement success and preservation |

## E1 — Model comparison

**Goal:** Compare Codex CLI with GPT against Claude Code with Claude.

**Change:** Change only the model and harness.

**Keep fixed:** Use one IR, prompt, reasoning level, description policy, sample,
and response schema.

**Procedure:** Send each single semantic case and clean control to both configurations. Request
structured findings. Do not repair the diagram.

**Metrics:** Report precision, recall, macro F1, false-positive rate,
localization, latency, and usage. Use F1 and false-positive rate to select the
winner. Use latency and usage to resolve a close result.

## E2 — IR comparison

**Goal:** Find the best input representation for the E1 winner.

**Change:** Compare Pydantic, Pydantic JSON, YAML, Mermaid, and compact JSON.

**Keep fixed:** Use the same model, prompt, reasoning level, descriptions,
cases, and response schema.

**Procedure:** Serialize each case into every IR. Request detection only.

**Metrics:** Report macro F1, false-positive rate, localization, latency, and
usage. Select the cheaper IR when two IRs have a similar score.

## E3 — Reasoning ablation

**Goal:** Measure the value of additional reasoning.

**Change:** Compare the supported low, medium, and high settings.

**Keep fixed:** Use the E1 model, E2 IR, prompt, descriptions, cases, and
response schema.

**Procedure:** Use one fixed sample. Include one case for each semantic
operator and clean controls. Do not repair the diagram.

**Metrics:** Report paired changes in F1, localization, latency, and usage.
Select the lowest setting that gives a result close to the best result.

## E4 — Description ablation

**Goal:** Measure the value of the reference process description.

**Change:** Compare diagram-only input against diagram and description input.

**Keep fixed:** Use the E1 model, E2 IR, E3 reasoning level, prompt, cases, and
response schema.

**Procedure:** Run both conditions for every case in the E3 sample. Pair the
results by case. Do not repair the diagram.

**Metrics:** Report paired changes in F1, recall, false-positive rate,
localization, latency, and usage.

## E5 — Validator ablation

**Goal:** Test if an LLM can replace the structural and formal validators.

**Change:** Compare these pipelines:

1. Tier 1 rules and Tier 2 formal checks.
2. Holistic LLM validation without deterministic findings.
3. Tier 1, Tier 2, and holistic LLM validation.

**Keep fixed:** Use the same cases, ground truth, scoring code, model, IR, and
prompt.

**Procedure:** Use single structural defects, single formal defects, and clean
controls. Request detection only.

**Metrics:** Report precision, recall, F1, false-positive rate, localization,
latency, and usage. Score each pipeline against injected ground truth.

## E6 — Detection benchmark

**Goal:** Measure the final frozen system on the complete benchmark.

**Configuration:** Freeze the decisions from E1 to E5. E6 reuses the same
corpus, so it is a complete-dataset comparison, not an unseen-data estimate.
Do not change the configuration after inspection of E6 results.

**Procedure:** Run the final configuration on every semantic, structural,
formal, paired, and clean case. Report each defect group separately. If quota
prevents completion, resume the same spec; do not replace it with a subsample.

For paired cases, calculate these measures:

- `any_detected`: The system found at least one injected defect.
- `all_detected`: The system found both injected defects.
- `exact_set`: The system found both defects and no additional category.

Match predictions to defects one-to-one. A single prediction cannot match two
defects. A repeated pair needs two findings with separate locations.

**Metrics:** Report overall and per-group precision, recall, F1,
false-positive rate, category accuracy, localization, latency, and usage.

## E7 — Repair experiment

**Goal:** Test if the final system removes detected defects without unwanted
changes.

**Eligibility:** Freeze a small repair sample after E6. A single case needs one
correct category and location. A paired case needs both correct categories and
locations.

Include these repair groups:

- single semantic, structural, and formal defects;
- disjoint semantic, structural, and formal pairs;
- interacting structural and formal pairs.

The current dataset has no interacting semantic pair group.

**Procedure:** Use this sequence for each eligible case:

1. Give the diagram and validated findings to the repair model.
2. Give the reference description and semantic evidence for semantic defects.
3. Generate one atomic repair plan for all findings in the case.
4. Apply the operations.
5. Run structural, formal, and semantic validation again.
6. Check target removal and unrelated-element preservation.

Do not repair each defect in a separate copy. One repair can affect the other
defect. Record the result after the first plan and after the bounded loop.

For single cases, report target removal and complete repair success. For paired
cases, also report these measures:

- `one_resolved`: The repair removed one injected defect.
- `both_resolved`: The repair removed both injected defects.
- new-error rate;
- separate results for disjoint and interacting pairs.

Do not require the reference XML or the reference edit sequence. Different
repairs can be correct. Check validity, target removal, and preservation with
code where possible. Send unclear semantic repairs to a blinded manual review.

## E8 — Enhancement/refinement experiment

E8 evaluates requirement-driven process model enhancement. The model receives
a valid `M_core` and a new chat instruction. It receives no semantic finding,
reference diagram, or oracle plan. This differs from detection, which asks the
model to report a contradiction against a complete description, and from
repair, which gives the model a detected and validated finding. Although E8
cases are constructed by reversing verified semantic mutations, they are
presented to the evaluated model as change requests. The experiment is thus a
synthetic refinement benchmark rather than a sample of organic user changes.
The stored mutation and inverse operations are construction and audit traces.
They are not operations performed by semantic validation, and the evaluated
model does not have to reproduce them.

Use `data/eval/enhancement/cases.json`. Each case supplies a valid `M_core`, a
single-turn `instruction` from held-out `D_extra`, the clean seed reference,
the relation contract, preservation IDs, and a replayable oracle plan. Run
`experiments/run_e8.py` through `app.services.chat.chat_diagram`; `--mock`
rehearses this same path without a provider and checkpoints JSONL by case ID.
For the live run, pass the frozen E1-E3 winner as `--config CONFIG.json`.
Analyze with `experiments/analyze_e8.py`. Score returned diagrams, Tier 1,
Tier 2 when available, deterministic relation satisfaction, preservation,
unnecessary changes, secondary GED, latency, usage, and manual review. Do not
require byte identity or exact oracle operations. Unsupported or ambiguous
semantic relations remain `manual_review`, not automatic failures.

The fixed slice has 21 cases. It contains six missing-task cases (`M01`) and
three cases for each other included relation: wrong ordering (`M02`), wrong
branch condition (`M03`), missing exception path (`M04`), unreachable outcome
(`M06`), and unwanted action (`M07`). Parallelism is not included because v1.0
has no human-verified, replayable semantic case for it. The stored relations are
accepted construction evidence; a result needs manual review only when its
relation cannot be scored deterministically.

## Execution order

1. Run a free mock rehearsal.
2. Run E1 and select the model.
3. Run E2 and select the IR.
4. Run E3 and E4 on the small sample.
5. Run E5 on structural and formal cases.
6. Freeze the final configuration.
7. Run E6 on the complete dataset with the frozen configuration.
8. Freeze the eligible repair sample.
9. Run E7.
10. Run E8 with the frozen winning model, IR, and reasoning level.
11. Produce the final analysis.

Checkpoint each completed response. Interleave configurations in a fixed
order. Retry transport errors only under one declared policy. Do not regenerate
a malformed or incorrect model answer.
