# One-word entrypoints. Host-run tooling (ingest/dbt) talks to the compose stack over
# localhost; the stack itself is `docker compose`. `make pipeline` is the offline
# reproducibility check (CLAUDE.md rule #4).
.PHONY: help up down logs seed ingest dbt dashboard dashboard_v1 dashboard_v2 pipeline test lint parse setup clean airflow

# Load .env if present so every target sees the same config.
ifneq (,$(wildcard .env))
include .env
export
endif

VENV    := .venv
PY      := $(VENV)/bin/python
DBT     := $(VENV)/bin/dbt
DBTDIR  := dbt
export DBT_PROFILES_DIR := $(abspath dbt)

help:  ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

setup:  ## Create venv + install all deps (incl. dbt, dashboard) and the dbt profile
	uv venv --python 3.12
	uv pip install -e ".[dbt,dashboard,dev]"
	cp -n .env.example .env || true
	cp -n dbt/profiles.example.yml dbt/profiles.yml || true

up:  ## Start the local stack (postgres, minio) and create buckets
	docker compose up -d postgres minio minio-init

down:  ## Stop the stack (keep volumes)
	docker compose down

logs:  ## Tail stack logs
	docker compose logs -f

seed:  ## Upload sample run files into MinIO landing bucket
	$(PY) -m ingestion.seed_storage

ingest:  ## Extract from object storage + load into raw Postgres (idempotent)
	$(PY) -m ingestion

dbt:  ## dbt build = run + test (same models everywhere; target via DBT_TARGET)
	cd $(DBTDIR) && $(abspath $(DBT)) build --target $${DBT_TARGET:-local}

dashboard_v1:  ## v1 dashboard (port 8501)
	$(VENV)/bin/streamlit run dashboard/app.py --server.port 8501

dashboard_v2:  ## v2 dashboard (port 8502)
	$(VENV)/bin/streamlit run dashboard/app_v2.py --server.port 8502

dashboard:  ## Launch both dashboards in background (v1=8501, v2=8502)
	$(VENV)/bin/streamlit run dashboard/app.py --server.port 8501 &
	$(VENV)/bin/streamlit run dashboard/app_v2.py --server.port 8502 &

pipeline: up seed ingest dbt  ## Full chain end-to-end (offline reproducibility check)
	@echo "pipeline complete — run 'make dashboard' to view"

airflow:  ## Start Airflow (optional, profile) at http://localhost:8080
	docker compose --profile airflow up -d

test:  ## Run python unit tests (DB-integration tests skip without POSTGRES_HOST)
	$(PY) -m pytest -q

lint:  ## Lint python with ruff
	$(VENV)/bin/ruff check ingestion dashboard tests

parse:  ## Validate the dbt project without a warehouse connection
	cd $(DBTDIR) && $(abspath $(DBT)) parse --target $${DBT_TARGET:-local}

clean:  ## Remove dbt artifacts
	rm -rf $(DBTDIR)/target $(DBTDIR)/dbt_packages $(DBTDIR)/logs
