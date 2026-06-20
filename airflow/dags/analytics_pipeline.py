"""Airflow DAG: object storage -> raw -> dbt build -> Parquet export.

Orchestration is deliberately kept for the skill, not because the cadence needs it
(CLAUDE.md Cost section) — `make pipeline` runs the same chain without Airflow. The DAG
mirrors that chain so the orchestrated and local paths can't diverge: each task shells
out to (or imports) the SAME modules the Makefile calls.

dbt models render as individual Airflow tasks via Cosmos (DbtTaskGroup), so the Airflow
UI shows the full model dependency graph instead of a single opaque "dbt_build" task.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from cosmos import DbtTaskGroup, ExecutionConfig, ProfileConfig, ProjectConfig

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
    description="run.json (S3) -> raw Postgres -> dbt star -> marts Parquet",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["rag-bench", "analytics"],
) as dag:

    def _extract_load() -> dict:
        from ingestion.config import PostgresConfig, StorageConfig
        from ingestion.load_raw import run
        from ingestion.storage import get_storage

        storage = get_storage(StorageConfig.from_env())
        return run(PostgresConfig.from_env(), storage)

    def _export() -> dict:
        from serve.export_marts import export
        return export()

    extract_load = PythonOperator(
        task_id="extract_load_raw",
        python_callable=_extract_load,
    )

    dbt_build = DbtTaskGroup(
        group_id="dbt_build",
        project_config=PROJECT_CONFIG,
        profile_config=PROFILE_CONFIG,
        execution_config=EXECUTION_CONFIG,
    )

    export_marts = PythonOperator(
        task_id="export_marts",
        python_callable=_export,
    )

    extract_load >> dbt_build >> export_marts
