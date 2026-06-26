"""Seed the object-storage landing zone from the local fixtures (``make seed``).

Uploads everything in LOCAL_SOURCE_DIR (the committed ingestion_sample/) into the S3 /
MinIO landing bucket so the rest of the pipeline can run against object storage exactly
as it would in cloud. No-op-friendly: overwrites are fine (idempotent by key).
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import StorageConfig
from .storage import MANIFEST_SUFFIX, RECORDS_SUFFIX, REFERENCE_SUBDIR

log = logging.getLogger("ingestion.seed_storage")


def _ensure_bucket(s3, bucket: str, region: str) -> None:
    """Create the bucket if absent; no-op if it already exists.

    Real AWS S3 requires a CreateBucketConfiguration.LocationConstraint for any region
    other than us-east-1 (MinIO tolerates either). Only the already-exists / already-owned
    cases are swallowed — any other error (e.g. AccessDenied) propagates loudly rather
    than being hidden. In cloud, Terraform owns the buckets (ADR-001); these calls then
    no-op via BucketAlreadyOwnedByYou, so this stays a local/CI convenience.
    """
    kwargs: dict[str, object] = {"Bucket": bucket}
    if region and region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        s3.create_bucket(**kwargs)
    except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
        pass


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = StorageConfig.from_env()
    src = Path(cfg.local_source_dir)
    if not src.is_dir():
        raise FileNotFoundError(f"LOCAL_SOURCE_DIR not found: {src}")

    import boto3

    s3 = boto3.client(
        "s3", endpoint_url=cfg.endpoint_url, region_name=cfg.region
    )
    # Ensure the landing bucket exists (idempotent) so CI / fresh MinIO needn't run the
    # mc init job. (Marts are served direct from Postgres — no marts bucket; ADR-001.)
    _ensure_bucket(s3, cfg.landing_bucket, cfg.region)

    prefix = cfg.landing_prefix.rstrip("/") + "/" if cfg.landing_prefix else ""
    # Run files may sit in dated batch subdirs (recursive); reference/ is uploaded
    # separately. Keys flatten under runs/ — run_ids are unique, so no collisions.
    run_files = sorted(
        p for p in src.rglob("*")
        if p.is_file()
        and (p.name.endswith(MANIFEST_SUFFIX) or p.suffix == RECORDS_SUFFIX)
        and REFERENCE_SUBDIR not in p.relative_to(src).parts
    )
    for p in run_files:
        s3.upload_file(str(p), cfg.landing_bucket, f"{prefix}{p.name}")
    log.info("seeded %d run files into s3://%s/%s", len(run_files), cfg.landing_bucket, prefix)

    # Shared reference inputs (questions.jsonl + corpus profiles) land under reference/.
    reference_dir = src / REFERENCE_SUBDIR
    reference_prefix = cfg.reference_prefix.rstrip("/") + "/" if cfg.reference_prefix else ""
    reference_files = (
        sorted(p for p in reference_dir.iterdir() if p.is_file()) if reference_dir.is_dir() else []
    )
    for p in reference_files:
        s3.upload_file(str(p), cfg.landing_bucket, f"{reference_prefix}{p.name}")
    log.info(
        "seeded %d reference files into s3://%s/%s",
        len(reference_files), cfg.landing_bucket, reference_prefix,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
