PY ?= .venv/bin/python

.PHONY: setup ingest verify api web dev build test e2e eval postman clean

setup:            ## create venv, install Python + JS dependencies
	uv venv .venv --python 3.12 || python3 -m venv .venv
	uv pip install --python $(PY) -e ".[ingest,llm,dev]" || $(PY) -m pip install -e ".[ingest,llm,dev]"
	npm --prefix frontend install

ingest:           ## rebuild the dataset snapshot from the PDFs in dataset/
	$(PY) -m ingestion build --raw dataset --out data/published

verify:           ## re-run publication checks on the current snapshot
	$(PY) -m ingestion verify

api:              ## run the API (serves the built dashboard too) on :8000
	.venv/bin/uvicorn backend.app.api.main:app --port 8000 --reload

web:              ## run the dashboard dev server on :5173 (proxies /api -> :8000)
	npm --prefix frontend run dev

build:            ## production build of the dashboard (served by the API)
	npm --prefix frontend run build

test:             ## unit, property, NLP and integration tests
	$(PY) -m pytest

e2e: build        ## browser tests (Playwright)
	cd frontend && npx playwright install chromium && npx playwright test

eval:             ## evaluation report -> docs/evaluation.md
	$(PY) -m tests.evaluation.run_eval

postman:          ## regenerate docs/openapi.json and the Postman collection
	$(PY) scripts/export_api.py

clean:
	rm -rf data/staging data/app.db frontend/dist
