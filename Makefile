ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

.PHONY: backend frontend test

backend:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload

frontend:
	cd $(ROOT)/frontend && npm run dev

test:
	cd $(ROOT) && PYTHONPATH=backend .venv/bin/pytest -v
