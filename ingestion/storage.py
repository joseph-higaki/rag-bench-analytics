"""Storage abstraction — the local<->cloud swap point (CLAUDE.md: isolate swap points).

The rest of the ingestion code asks a ``Storage`` for runs, manifests, records and
questions. It never knows whether the bytes came from a local directory or S3/MinIO.
Local dev uses ``LocalStorage``; cloud uses ``S3Storage``; both satisfy the same Protocol.

File-naming contract (owned by the benchmark producer):
  <run_id>.manifest.json   one run-level metadata object
  <run_id>.jsonl           one scored-answer record per line (grain: run x question)
  questions.jsonl          shared question bank, joined at transform time
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from .config import StorageConfig

MANIFEST_SUFFIX = ".manifest.json"
RECORDS_SUFFIX = ".jsonl"
QUESTIONS_NAME = "questions.jsonl"


def _iter_jsonl(text: str) -> Iterator[dict]:
    """Yield one dict per non-blank line. Tolerates trailing newlines / blank lines."""
    for line in text.splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


class Storage(Protocol):
    """Read-only view over the landing zone. Implementations must be cheap to construct."""

    def list_run_ids(self) -> list[str]: ...
    def source_uri(self, run_id: str) -> str: ...
    def read_manifest(self, run_id: str) -> dict: ...
    def read_records(self, run_id: str) -> Iterator[dict]: ...
    def read_questions(self) -> Iterator[dict]: ...
    def has_questions(self) -> bool: ...


class LocalStorage:
    """Reads run files from a local directory. Used for offline dev + CI fixtures."""

    def __init__(self, source_dir: str | Path):
        self.root = Path(source_dir)
        if not self.root.is_dir():
            raise FileNotFoundError(f"LOCAL_SOURCE_DIR does not exist: {self.root}")

    def list_run_ids(self) -> list[str]:
        return sorted(
            p.name[: -len(MANIFEST_SUFFIX)] for p in self.root.glob(f"*{MANIFEST_SUFFIX}")
        )

    def source_uri(self, run_id: str) -> str:
        return (self.root / f"{run_id}{MANIFEST_SUFFIX}").resolve().as_uri()

    def read_manifest(self, run_id: str) -> dict:
        return json.loads((self.root / f"{run_id}{MANIFEST_SUFFIX}").read_text())

    def read_records(self, run_id: str) -> Iterator[dict]:
        yield from _iter_jsonl((self.root / f"{run_id}{RECORDS_SUFFIX}").read_text())

    def read_questions(self) -> Iterator[dict]:
        yield from _iter_jsonl((self.root / QUESTIONS_NAME).read_text())

    def has_questions(self) -> bool:
        return (self.root / QUESTIONS_NAME).is_file()


class S3Storage:
    """Reads run files from S3 (or MinIO via endpoint_url). Same Protocol as LocalStorage."""

    def __init__(self, cfg: StorageConfig):
        import boto3  # imported lazily so local dev needn't pay the import

        self.bucket = cfg.landing_bucket
        self.prefix = cfg.landing_prefix.rstrip("/") + "/" if cfg.landing_prefix else ""
        self._s3 = boto3.client(
            "s3",
            endpoint_url=cfg.endpoint_url,  # None => real AWS
            region_name=cfg.region,
        )

    def _key(self, name: str) -> str:
        return f"{self.prefix}{name}"

    def _get_text(self, name: str) -> str:
        obj = self._s3.get_object(Bucket=self.bucket, Key=self._key(name))
        return obj["Body"].read().decode("utf-8")

    def list_run_ids(self) -> list[str]:
        paginator = self._s3.get_paginator("list_objects_v2")
        run_ids: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(MANIFEST_SUFFIX):
                    name = key[len(self.prefix):]
                    run_ids.append(name[: -len(MANIFEST_SUFFIX)])
        return sorted(run_ids)

    def source_uri(self, run_id: str) -> str:
        return f"s3://{self.bucket}/{self._key(run_id + MANIFEST_SUFFIX)}"

    def read_manifest(self, run_id: str) -> dict:
        return json.loads(self._get_text(run_id + MANIFEST_SUFFIX))

    def read_records(self, run_id: str) -> Iterator[dict]:
        yield from _iter_jsonl(self._get_text(run_id + RECORDS_SUFFIX))

    def read_questions(self) -> Iterator[dict]:
        yield from _iter_jsonl(self._get_text(QUESTIONS_NAME))

    def has_questions(self) -> bool:
        try:
            self._s3.head_object(Bucket=self.bucket, Key=self._key(QUESTIONS_NAME))
            return True
        except Exception:
            return False


def get_storage(cfg: StorageConfig) -> Storage:
    """Factory: the one place that picks an implementation from config."""
    if cfg.backend == "local":
        return LocalStorage(cfg.local_source_dir)
    return S3Storage(cfg)
