# Import fixtures

Small BPMN files the test suite loads from disk. Unlike `../test_cases/`, these
are not demonstration diagrams — nothing here is meant to be opened in the UI.

| File | Used for |
|---|---|
| `duplicate_id.bpmn` | the document-scoped id uniqueness check rejecting at parse |
| `pmo_01.bpmn` | a realistic, tier-1-valid diagram with BPMN as the *default* namespace — the CLI end-to-end tests and the namespace round-trip test |

## Why `pmo_01.bpmn` is copied here

It is `bpmn/01.bpmn` from the PMo dataset, byte-identical. The dataset itself is
a 7.8 MB download and stays out of version control (see `.gitignore` and
`../pmo-dataset/SOURCE.md`), but four tests depended on that one path, so a fresh
clone could not run the suite without first fetching from Zenodo. Copying the
single file it needed keeps the suite self-contained and the dataset a cache.

Do not add more of the dataset here. Phase 3a curates the benchmark from
`../pmo-dataset/` directly, and that stays untracked.
