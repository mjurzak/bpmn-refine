ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
PROBE ?= data/eval/v1.0/probe.json
TIER2 ?= off

.PHONY: backend frontend test test-backend test-frontend docs-check dry-run experiment experiment-rehearse eligibility-probe dataset-build dataset-refresh-manifest dataset-audit enhancement-build enhancement-audit e8-rehearse

backend:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload

frontend:
	cd $(ROOT)/frontend && npm run dev

test: docs-check test-backend test-frontend

test-backend:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/pytest -v

test-frontend:
	cd $(ROOT)/frontend && npm test

docs-check:
	cd $(ROOT) && .venv/bin/python scripts/check_docs.py

# every ablation control against mocked providers, no paid API call
dry-run:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/python -m app.cli dry-run --out $(OUT)

# resumable: re-running the same SPEC/OUT skips what is already on disk
experiment:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/python -m app.cli run-experiment --spec $(SPEC) --out $(OUT) --concurrency $(or $(CONCURRENCY),1)

# which source models qualify as dataset seeds — deterministic, no API call
eligibility-probe:
	cd $(ROOT) && PYTHONPATH=backend:. .venv/bin/python -m evaluation.generator.cli probe --out $(OUT)

# snapshot eligible seeds and emit deterministic S01-S03/F01-F04 variants
dataset-build:
	cd $(ROOT) && PYTHONPATH=backend:. .venv/bin/python -m evaluation.generator.cli build --probe $(PROBE) --out $(OUT) --dataset-version $(VERSION)

dataset-refresh-manifest:
	cd $(ROOT) && PYTHONPATH=backend:. .venv/bin/python -m evaluation.generator.cli refresh-manifest --dataset $(DATASET)

dataset-audit:
	cd $(ROOT) && PYTHONPATH=backend:. .venv/bin/python -m evaluation.generator.cli audit --dataset $(DATASET)

# deterministic E8 construction and provider-free audit
enhancement-build:
	@cd $(ROOT) && PYTHONPATH=backend:. .venv/bin/python -m evaluation.enhancement_cli build --dataset-root data/eval/v1.0 --out data/eval/enhancement --no-tier2

enhancement-audit:
	@cd $(ROOT) && PYTHONPATH=backend:. .venv/bin/python -m evaluation.enhancement_audit data/eval/enhancement/cases.json --root data/eval/enhancement --tier2 $(TIER2)

# actual chat/refinement orchestration with a deterministic mock response
e8-rehearse:
	@cd $(ROOT) && PYTHONPATH=backend:. .venv/bin/python experiments/run_e8.py data/eval/enhancement/cases.json --out $(OUT) --mock --no-resume

# same spec, canned responses, no API call — rehearse before paying
experiment-rehearse:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/python -m app.cli run-experiment --spec $(SPEC) --out $(OUT) --mock --no-resume --concurrency $(or $(CONCURRENCY),1)
