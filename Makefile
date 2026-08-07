ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

.PHONY: backend frontend test test-backend test-frontend dry-run experiment experiment-rehearse eligibility-probe

backend:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload

frontend:
	cd $(ROOT)/frontend && npm run dev

test: test-backend test-frontend

test-backend:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/pytest -v

test-frontend:
	cd $(ROOT)/frontend && npm test

# every ablation control against mocked providers, no paid API call
dry-run:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/python -m app.cli dry-run --out $(OUT)

# resumable: re-running the same SPEC/OUT skips what is already on disk
experiment:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/python -m app.cli run-experiment --spec $(SPEC) --out $(OUT)

# which source models qualify as dataset seeds — deterministic, no API call
eligibility-probe:
	cd $(ROOT) && PYTHONPATH=backend:. .venv/bin/python -m evaluation.generator.cli probe --out $(OUT)

# same spec, canned responses, no API call — rehearse before paying
experiment-rehearse:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/python -m app.cli run-experiment --spec $(SPEC) --out $(OUT) --mock --no-resume
