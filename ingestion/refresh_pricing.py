"""Refresh the committed model-pricing snapshot (``make refresh-pricing``) + the DAG fetch step.

Fetches Portkey's per-provider pricing catalog from the public internet and writes the snapshot
the pipeline consumes. Portkey is just a *source*: it lands in object storage like any other input
(`raw.model_pricing` → `stg_model_pricing_portkey`); there is no `pricing_source` toggle. Two write
targets share one builder (``build_snapshot``):

- ``make refresh-pricing`` (this module's CLI) writes the **committed git fixture**
  (`ingestion_sample/reference/pricing/<provider>.json`) — the offline artifact the repo defaults
  to. Run it, commit the diff, then `make seed ingest dbt`.
- The Airflow **fetch_prices** task writes straight to the landing zone via the storage backend
  (S3/MinIO); on failure it falls back to the last-landed snapshot (see the DAG).

The snapshot is **self-describing** — ``{"meta": {...}, "models": <portkey dict>}``. The
``meta.fetched_at`` field dates prices honestly (stable across re-ingests, unlike load time) and
is what SCD2 price-history will key on later. Written sorted+indented: a re-fetch diffs to price
changes only.

Pinnable source: ``--ref`` picks a Portkey git ref (default ``main``). The committed snapshot, not
the live fetch, is the reproducibility anchor, so a moving ``main`` is acceptable.
"""

from __future__ import annotations

import argparse
import json
import logging
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from .config import StorageConfig
from .storage import PRICING_SUBDIR, REFERENCE_SUBDIR

log = logging.getLogger("ingestion.refresh_pricing")

# Portkey-AI/models: MIT-licensed open catalog, one JSON per provider under pricing/ (ADR-006).
# raw.githubusercontent serves it at a pinnable ref. Prices are cents/token here; the cents->
# USD/Mtok conversion lives downstream in stg_model_pricing_portkey — this step only snapshots
# bytes (+ provenance), never transforms them.
PORTKEY_URL = "https://raw.githubusercontent.com/Portkey-AI/models/{ref}/pricing/{provider}.json"

# Probe path into a single model object. Its presence distinguishes a real catalog from an HTML
# error page or a moved schema — it's the exact path stg_model_pricing_portkey reads.
_PRICE_PATH = ("pricing_config", "pay_as_you_go", "request_token", "price")


def fetch_models(provider: str, ref: str) -> dict:
    """GET the provider catalog from Portkey. Raise on non-JSON or a non-object top level."""
    url = PORTKEY_URL.format(ref=ref, provider=provider)
    log.info("fetching %s", url)
    with urllib.request.urlopen(url, timeout=30) as resp:
        models = json.loads(resp.read().decode("utf-8"))
    if not isinstance(models, dict) or not models:
        raise ValueError(
            f"{provider}: expected a non-empty object of models, got {type(models).__name__}"
        )
    return models


def validate_shape(provider: str, models: dict) -> None:
    """Fail loud on schema drift before writing: at least one model must carry the pay_as_you_go
    price path stg_model_pricing_portkey reads. Catches an error page, a renamed pricing schema, or
    an empty catalog — any of which would silently null every cost downstream."""

    def has_price(obj: object) -> bool:
        cur: object = obj
        for key in _PRICE_PATH:
            if not isinstance(cur, dict) or key not in cur:
                return False
            cur = cur[key]
        return True

    if not any(isinstance(v, dict) and has_price(v) for v in models.values()):
        raise ValueError(
            f"{provider}: no model carries {'.'.join(_PRICE_PATH)} — Portkey's schema may have "
            "changed; refusing to write the snapshot (it would null all costs downstream)."
        )


def build_snapshot(provider: str, ref: str) -> dict:
    """Fetch + validate + wrap into the self-describing snapshot the pipeline lands. Shared by the
    CLI (git fixture) and the DAG fetch task (S3), so both produce byte-identical structure."""
    models = fetch_models(provider, ref)
    validate_shape(provider, models)
    return {
        "meta": {
            "provider": provider,
            "fetched_at": datetime.now(UTC).date().isoformat(),
            "ref": ref,
            "source_url": PORTKEY_URL.format(ref=ref, provider=provider),
            "model_count": len(models),
        },
        "models": models,
    }


def write_snapshot_file(path: Path, snapshot: dict) -> None:
    """Overwrite the committed git fixture, sorted+indented so a re-fetch diffs to price changes
    only (stable key order) rather than reshuffled JSON noise."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = StorageConfig.from_env()
    # The CLI always targets the committed git fixture (LOCAL_SOURCE_DIR), regardless of
    # STORAGE_BACKEND — its job is to produce the committable offline artifact. The DAG, not this
    # path, writes to S3.
    pricing_dir = Path(cfg.local_source_dir) / REFERENCE_SUBDIR / PRICING_SUBDIR

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "providers",
        nargs="*",
        help="Providers to refresh (e.g. anthropic). Default: the providers already snapshotted.",
    )
    parser.add_argument("--ref", default="main", help="Portkey git ref to fetch (default: main).")
    args = parser.parse_args(argv)

    # Default to refreshing whatever is already snapshotted, so the common case is just
    # `make refresh-pricing`; a new provider is an explicit argument.
    providers = args.providers or sorted(p.stem for p in pricing_dir.glob("*.json"))
    if not providers:
        parser.error(
            f"no providers given and none found under {pricing_dir} — "
            "pass one, e.g. `make refresh-pricing PROVIDERS=anthropic`."
        )

    for provider in providers:
        snapshot = build_snapshot(provider, args.ref)
        path = pricing_dir / f"{provider}.json"
        write_snapshot_file(path, snapshot)
        log.info(
            "wrote %d models (fetched_at=%s) -> %s",
            snapshot["meta"]["model_count"], snapshot["meta"]["fetched_at"], path,
        )

    log.info(
        "refreshed %d provider snapshot(s) @ ref=%s; commit the diff, then `make seed ingest dbt` "
        "(or `make pipeline`) to land them.",
        len(providers), args.ref,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
