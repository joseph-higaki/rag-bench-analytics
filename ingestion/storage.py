"""Storage abstraction — the local<->cloud swap point (CLAUDE.md: isolate swap points).

The rest of the ingestion code asks a ``Storage`` for runs, manifests, records and
questions. It never knows whether the bytes came from a local directory or S3/MinIO.
Local dev uses ``LocalStorage``; cloud uses ``S3Storage``; both satisfy the same Protocol.

File-naming contract (owned by the benchmark producer):
  <run_id>.manifest.json   one run-level metadata object
  <run_id>.jsonl           one scored-answer record per line (grain: run x question)
  questions.jsonl          shared question bank, joined at transform time

Layout: run files may sit in dated batch subdirs (e.g. 20260626T173352Z/) — discovered
recursively, keyed by run_id (batch dirs are local ergonomics; in S3 they flatten under
the runs/ prefix). Shared reference inputs (questions.jsonl + corpus profiles) live under
reference/ — not run-scoped, joined at transform time.
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
# Shared reference inputs (questions.jsonl + corpus profiles <corpus_build_id>.json) land
# under their own prefix/subdir — not run-keyed; joined at transform time.
REFERENCE_SUBDIR = "reference"
CORPUS_SUFFIX = ".json"
# External (non-benchmark) reference inputs land under reference/<PRICING_SUBDIR>/ — e.g. model
# token-pricing catalogs (Portkey's pricing/<provider>.json). Kept in a subdir so corpus discovery
# (which treats any reference/*.json as a corpus build) doesn't swallow them.
PRICING_SUBDIR = "pricing"


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
    def list_corpus_build_ids(self) -> list[str]: ...
    def corpus_source_uri(self, corpus_build_id: str) -> str: ...
    def read_corpus_profile(self, corpus_build_id: str) -> dict: ...
    def list_pricing_providers(self) -> list[str]: ...
    def pricing_source_uri(self, provider: str) -> str: ...
    def read_pricing_snapshot(self, provider: str) -> dict: ...
    def write_pricing_snapshot(self, provider: str, snapshot: dict) -> str: ...


class LocalStorage:
    """Reads run files from a local directory tree. Used for offline dev + CI fixtures.

    Runs are discovered recursively (dated batch subdirs allowed) and keyed by run_id; the
    reference/ subtree is excluded from run discovery (it holds shared, non-run inputs)."""

    def __init__(self, source_dir: str | Path):
        self.root = Path(source_dir)
        if not self.root.is_dir():
            raise FileNotFoundError(f"LOCAL_SOURCE_DIR does not exist: {self.root}")
        self.reference_dir = self.root / REFERENCE_SUBDIR
        self.pricing_dir = self.reference_dir / PRICING_SUBDIR
        # run_id -> manifest path (recursive; reference/ excluded). A run's .jsonl is the
        # manifest's sibling in the same batch dir.
        self._manifests: dict[str, Path] = {
            p.name[: -len(MANIFEST_SUFFIX)]: p
            for p in self.root.rglob(f"*{MANIFEST_SUFFIX}")
            if REFERENCE_SUBDIR not in p.relative_to(self.root).parts
        }

    def list_run_ids(self) -> list[str]:
        return sorted(self._manifests)

    def source_uri(self, run_id: str) -> str:
        return self._manifests[run_id].resolve().as_uri()

    def read_manifest(self, run_id: str) -> dict:
        return json.loads(self._manifests[run_id].read_text())

    def read_records(self, run_id: str) -> Iterator[dict]:
        records = self._manifests[run_id].with_name(f"{run_id}{RECORDS_SUFFIX}")
        yield from _iter_jsonl(records.read_text())

    def read_questions(self) -> Iterator[dict]:
        yield from _iter_jsonl((self.reference_dir / QUESTIONS_NAME).read_text())

    def has_questions(self) -> bool:
        return (self.reference_dir / QUESTIONS_NAME).is_file()

    def list_corpus_build_ids(self) -> list[str]:
        if not self.reference_dir.is_dir():
            return []
        return sorted(p.stem for p in self.reference_dir.glob(f"*{CORPUS_SUFFIX}"))

    def corpus_source_uri(self, corpus_build_id: str) -> str:
        return (
            self.reference_dir / f"{corpus_build_id}{CORPUS_SUFFIX}"
        ).resolve().as_uri()

    def read_corpus_profile(self, corpus_build_id: str) -> dict:
        return json.loads(
            (self.reference_dir / f"{corpus_build_id}{CORPUS_SUFFIX}").read_text()
        )

    def list_pricing_providers(self) -> list[str]:
        # One snapshot file per provider (reference/pricing/<provider>.json); stem = provider.
        if not self.pricing_dir.is_dir():
            return []
        return sorted(p.stem for p in self.pricing_dir.glob(f"*{CORPUS_SUFFIX}"))

    def pricing_source_uri(self, provider: str) -> str:
        return (self.pricing_dir / f"{provider}{CORPUS_SUFFIX}").resolve().as_uri()

    def read_pricing_snapshot(self, provider: str) -> dict:
        return json.loads((self.pricing_dir / f"{provider}{CORPUS_SUFFIX}").read_text())

    def write_pricing_snapshot(self, provider: str, snapshot: dict) -> str:
        # Sorted+indented to match the refresh CLI's git-fixture format (price-only diffs).
        path = self.pricing_dir / f"{provider}{CORPUS_SUFFIX}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        return path.resolve().as_uri()


class S3Storage:
    """Reads run files from S3 (or MinIO via endpoint_url). Same Protocol as LocalStorage."""

    def __init__(self, cfg: StorageConfig):
        import boto3  # imported lazily so local dev needn't pay the import

        self.bucket = cfg.landing_bucket
        self.prefix = cfg.landing_prefix.rstrip("/") + "/" if cfg.landing_prefix else ""
        self.reference_prefix = (
            cfg.reference_prefix.rstrip("/") + "/" if cfg.reference_prefix else ""
        )
        self.pricing_prefix = f"{self.reference_prefix}{PRICING_SUBDIR}/"
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
        obj = self._s3.get_object(
            Bucket=self.bucket, Key=f"{self.reference_prefix}{QUESTIONS_NAME}"
        )
        yield from _iter_jsonl(obj["Body"].read().decode("utf-8"))

    def has_questions(self) -> bool:
        try:
            self._s3.head_object(
                Bucket=self.bucket, Key=f"{self.reference_prefix}{QUESTIONS_NAME}"
            )
            return True
        except Exception:
            return False

    def list_corpus_build_ids(self) -> list[str]:
        # reference/ holds questions.jsonl too, but it ends in .jsonl (not .json) — excluded.
        paginator = self._s3.get_paginator("list_objects_v2")
        ids: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.reference_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.startswith(self.pricing_prefix):
                    continue  # external pricing snapshots live under reference/pricing/, not corpus
                if key.endswith(CORPUS_SUFFIX):
                    name = key[len(self.reference_prefix):]
                    ids.append(name[: -len(CORPUS_SUFFIX)])
        return sorted(ids)

    def corpus_source_uri(self, corpus_build_id: str) -> str:
        return f"s3://{self.bucket}/{self.reference_prefix}{corpus_build_id}{CORPUS_SUFFIX}"

    def read_corpus_profile(self, corpus_build_id: str) -> dict:
        obj = self._s3.get_object(
            Bucket=self.bucket,
            Key=f"{self.reference_prefix}{corpus_build_id}{CORPUS_SUFFIX}",
        )
        return json.loads(obj["Body"].read().decode("utf-8"))

    def list_pricing_providers(self) -> list[str]:
        paginator = self._s3.get_paginator("list_objects_v2")
        providers: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.pricing_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(CORPUS_SUFFIX):
                    providers.append(key[len(self.pricing_prefix): -len(CORPUS_SUFFIX)])
        return sorted(providers)

    def pricing_source_uri(self, provider: str) -> str:
        return f"s3://{self.bucket}/{self.pricing_prefix}{provider}{CORPUS_SUFFIX}"

    def read_pricing_snapshot(self, provider: str) -> dict:
        obj = self._s3.get_object(
            Bucket=self.bucket, Key=f"{self.pricing_prefix}{provider}{CORPUS_SUFFIX}"
        )
        return json.loads(obj["Body"].read().decode("utf-8"))

    def write_pricing_snapshot(self, provider: str, snapshot: dict) -> str:
        # The DAG fetch_prices task lands a fresh snapshot here; load_raw then ingests it like any
        # other landed input. No git involved in cloud — object storage is the source of record.
        key = f"{self.pricing_prefix}{provider}{CORPUS_SUFFIX}"
        self._s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=(json.dumps(snapshot, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            ContentType="application/json",
        )
        return f"s3://{self.bucket}/{key}"


def get_storage(cfg: StorageConfig) -> Storage:
    """Factory: the one place that picks an implementation from config."""
    if cfg.backend == "local":
        return LocalStorage(cfg.local_source_dir)
    return S3Storage(cfg)
