"""Seed the object-storage landing zone from the local fixtures (``make seed``).

Uploads everything in LOCAL_SOURCE_DIR (the committed ingestion_sample/) into the S3 /
MinIO landing bucket so the rest of the pipeline can run against object storage exactly
as it would in cloud. No-op-friendly: overwrites are fine (idempotent by key).
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import StorageConfig

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
    # Ensure buckets exist (idempotent) so CI / fresh MinIO needn't run the mc init job.
    for bucket in (cfg.landing_bucket, cfg.marts_bucket):
        _ensure_bucket(s3, bucket, cfg.region)

    prefix = cfg.landing_prefix.rstrip("/") + "/" if cfg.landing_prefix else ""
    files = sorted(p for p in src.iterdir() if p.is_file())
    for p in files:
        key = f"{prefix}{p.name}"
        s3.upload_file(str(p), cfg.landing_bucket, key)
    log.info("seeded %d files into s3://%s/%s", len(files), cfg.landing_bucket, prefix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
