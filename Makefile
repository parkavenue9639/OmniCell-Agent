PYTHON ?= python3
BACKEND_PYTHON := $(CURDIR)/.venv/bin/python
BACKEND_SOURCE := $(CURDIR)/backend/src

.PHONY: dev db-migrate db-check e2e-live-inspect

dev:
	@$(PYTHON) scripts/dev.py

db-migrate:
	@PYTHONPATH="$(BACKEND_SOURCE)$${PYTHONPATH:+:$$PYTHONPATH}" \
		"$(BACKEND_PYTHON)" -m omnicell_agent.persistence.cli migrate

db-check:
	@PYTHONPATH="$(BACKEND_SOURCE)$${PYTHONPATH:+:$$PYTHONPATH}" \
		"$(BACKEND_PYTHON)" -m omnicell_agent.persistence.cli check

e2e-live-inspect:
	@cd frontend && npm run test:e2e:live:inspect
