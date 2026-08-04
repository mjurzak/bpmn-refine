ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

.PHONY: backend frontend test test-backend test-frontend dry-run experiment experiment-rehearse

backend:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload

frontend:
	cd $(ROOT)/frontend && npm run dev

# both suites; `test-backend` / `test-frontend` run one at a time
test: test-backend test-frontend

test-backend:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/pytest -v

test-frontend:
	cd $(ROOT)/frontend && npm test

# exercises every ablation control against mocked providers and writes a
# self-contained run record. Makes no paid API call.
dry-run:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/python -m app.cli dry-run --out $(OUT)

# runs a sweep against real providers and persists one record per trial.
# Resumable: re-running the same SPEC/OUT skips what is already on disk.
experiment:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/python -m app.cli run-experiment --spec $(SPEC) --out $(OUT)

# same spec, canned responses, no API call — rehearse before paying
experiment-rehearse:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/python -m app.cli run-experiment --spec $(SPEC) --out $(OUT) --mock --no-resume
