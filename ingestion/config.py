"""Environment-driven configuration. Secrets via env only (CLAUDE.md golden rule #5).

Nothing here is benchmark-specific; it only describes *where* files land and *which*
warehouse to write to. Local vs cloud differ only by these values, never by code path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Local dev reads .env; in cloud the platform injects env and there is no .env file.
load_dotenv()


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required env var {name!r} is unset. See .env.example.")
    return val


@dataclass(frozen=True)
class StorageConfig:
    backend: str  # "local" | "s3"
    # local
    local_source_dir: str
    # s3 / minio
    endpoint_url: str | None
    region: str
    landing_bucket: str
    landing_prefix: str

    @classmethod
    def from_env(cls) -> StorageConfig:
        backend = os.environ.get("STORAGE_BACKEND", "local").lower()
        if backend not in ("local", "s3"):
            raise RuntimeError(f"STORAGE_BACKEND must be 'local' or 's3', got {backend!r}")
        # Empty string -> None so boto3 talks to real AWS, not a bogus endpoint.
        endpoint = os.environ.get("S3_ENDPOINT_URL") or None
        return cls(
            backend=backend,
            local_source_dir=os.environ.get("LOCAL_SOURCE_DIR", "./ingestion_sample"),
            endpoint_url=endpoint,
            region=os.environ.get("AWS_REGION", "us-east-1"),
            landing_bucket=os.environ.get("S3_LANDING_BUCKET", "rag-bench-landing"),
            landing_prefix=os.environ.get("S3_LANDING_PREFIX", "runs/"),
        )


@dataclass(frozen=True)
class PostgresConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str
    raw_schema: str

    @classmethod
    def from_env(cls) -> PostgresConfig:
        return cls(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            dbname=os.environ.get("POSTGRES_DB", "analytics"),
            user=os.environ.get("POSTGRES_USER", "analytics"),
            password=_require("POSTGRES_PASSWORD"),
            raw_schema=os.environ.get("RAW_SCHEMA", "raw"),
        )

    @property
    def conninfo(self) -> str:
        return (
            f"host={self.host} port={self.port} dbname={self.dbname} "
            f"user={self.user} password={self.password}"
        )
