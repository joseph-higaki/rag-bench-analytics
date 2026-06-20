"""Export the dbt marts to Parquet in object storage (``make export``).

The serving boundary: the dashboard reads Parquet from S3, never the warehouse (CLAUDE.md
— the warehouse need not be internet-reachable). Runs after `dbt build`. Reads ONLY the
marts schema (rule #2), one Parquet object per table under the marts prefix.
"""

from __future__ import annotations

import io
import logging
import os

import boto3
import pandas as pd
import psycopg

from ingestion.config import PostgresConfig, StorageConfig

log = logging.getLogger("serve.export_marts")

# The star: the fact plus its dimensions. These are the only tables the dashboard needs.
MART_TABLES = [
    "fct_scored_answer",
    "dim_run",
    "dim_question",
    "dim_generator",
    "dim_retriever_cond",
    "dim_judge",
    "dim_corpus",
]


def export() -> dict[str, int]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    pg = PostgresConfig.from_env()
    storage = StorageConfig.from_env()
    # marts live in <DBT_SCHEMA>_marts per dbt_project.yml (+schema: marts suffix).
    dbt_schema = os.environ.get("DBT_SCHEMA", "analytics")
    marts_schema = f"{dbt_schema}_marts"

    s3 = boto3.client(
        "s3", endpoint_url=storage.endpoint_url, region_name=storage.region
    )
    prefix = storage.marts_prefix.rstrip("/") + "/" if storage.marts_prefix else ""

    counts: dict[str, int] = {}
    with psycopg.connect(pg.conninfo) as conn:
        for table in MART_TABLES:
            df = pd.read_sql(f'select * from {marts_schema}."{table}"', conn)
            buf = io.BytesIO()
            df.to_parquet(buf, index=False)  # pyarrow engine
            buf.seek(0)
            key = f"{prefix}{table}.parquet"
            s3.put_object(Bucket=storage.marts_bucket, Key=key, Body=buf.getvalue())
            counts[table] = len(df)
            log.info(
                "exported %s (%d rows) -> s3://%s/%s",
                table, len(df), storage.marts_bucket, key,
            )
    return counts


if __name__ == "__main__":
    export()
