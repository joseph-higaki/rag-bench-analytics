"""Airflow DAG: fetch prices -> object storage -> raw -> dbt build (the marts star).

Orchestration is deliberately kept for the skill, not because the cadence needs it
(CLAUDE.md Cost section) — `make pipeline` runs the same chain without Airflow. The DAG
mirrors that chain so the orchestrated and local paths can't diverge: each task shells
out to (or imports) the SAME modules the Makefile calls. The dashboard reads the marts
schema directly (ADR-001), so there is no export step.

The `fetch_prices` task is the one step that reaches the public internet — it refreshes the
Portkey pricing snapshot in the landing zone before extract/load. It is **soft-fail**: on any
fetch error (no network, Portkey down, schema drift) it raises AirflowSkipException, and the
downstream tasks run anyway (`trigger_rule="none_failed"`) against the **last-landed snapshot**
(the committed offline artifact on a fresh deploy). So a price refresh never blocks the build,
and the build never depends on a live fetch (golden rule #4: the build path stays offline).

dbt models render as individual Airflow tasks via Cosmos (DbtTaskGroup), so the Airflow
UI shows the full model dependency graph instead of a single opaque "dbt_build" task.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.exceptions import AirflowSkipException
from airflow.operators.python import PythonOperator
from cosmos import DbtTaskGroup, ExecutionConfig, ProfileConfig, ProjectConfig

log = logging.getLogger(__name__)

# Portkey git ref the fetch pins to; the committed snapshot is the real reproducibility anchor.
PORTKEY_REF = os.environ.get("PORTKEY_REF", "main")

DBT_DIR = "/opt/airflow/dbt"
DBT_EXECUTABLE = "/opt/airflow/dbt_venv/bin/dbt"

PROJECT_CONFIG = ProjectConfig(DBT_DIR)

PROFILE_CONFIG = ProfileConfig(
    profile_name="rag_bench_analytics",
    target_name=os.environ.get("DBT_TARGET", "local"),
    profiles_yml_filepath=Path(f"{DBT_DIR}/profiles.yml"),
)

EXECUTION_CONFIG = ExecutionConfig(
    dbt_executable_path=DBT_EXECUTABLE,
)

default_args = {"retries": 1}

with DAG(
    dag_id="analytics_pipeline",
    description="run.json (S3) -> raw Postgres -> dbt star (marts)",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    # Serialize runs: every task rebuilds the same marts schema, so two concurrent
    # runs collide on Postgres catalog DDL ("tuple concurrently updated"). A manual
    # trigger racing the scheduler's latest interval run is the common trigger.
    max_active_runs=1,
    default_args=default_args,
    tags=["rag-bench", "analytics"],
) as dag:

    def _fetch_prices() -> dict:
        """Refresh each landed pricing snapshot from Portkey. Soft-fail: if every fetch fails,
        skip so the build proceeds on the last-landed snapshot. Partial success lands what it can."""
        from ingestion.config import StorageConfig
        from ingestion.refresh_pricing import build_snapshot
        from ingestion.storage import get_storage

        storage = get_storage(StorageConfig.from_env())
        providers = storage.list_pricing_providers()
        if not providers:
            log.warning("no pricing snapshots in landing zone to refresh; skipping")
            return {"refreshed": [], "failed": []}

        refreshed: list[str] = []
        failed: list[str] = []
        for provider in providers:
            try:
                storage.write_pricing_snapshot(provider, build_snapshot(provider, PORTKEY_REF))
                refreshed.append(provider)
            except Exception as exc:  # network, schema drift, etc. — fall back, don't fail the DAG
                log.warning("pricing refresh failed for %s: %s", provider, exc)
                failed.append(provider)

        if not refreshed:
            raise AirflowSkipException(
                f"all pricing fetches failed {failed}; building on the last-landed snapshot"
            )
        return {"refreshed": refreshed, "failed": failed}

    def _extract_load() -> dict:
        from ingestion.config import PostgresConfig, StorageConfig
        from ingestion.load_raw import run
        from ingestion.storage import get_storage

        storage = get_storage(StorageConfig.from_env())
        return run(PostgresConfig.from_env(), storage)

    fetch_prices = PythonOperator(
        task_id="fetch_prices",
        python_callable=_fetch_prices,
        retries=2,
    )

    extract_load = PythonOperator(
        task_id="extract_load_raw",
        python_callable=_extract_load,
        # Run even if fetch_prices skipped (soft-fail) — build on the last-landed snapshot.
        trigger_rule="none_failed",
    )

    dbt_build = DbtTaskGroup(
        group_id="dbt_build",
        project_config=PROJECT_CONFIG,
        profile_config=PROFILE_CONFIG,
        execution_config=EXECUTION_CONFIG,
    )

    fetch_prices >> extract_load >> dbt_build
