"""CLI entrypoint: ``python -m ingestion`` runs extract + load against the configured
storage backend and warehouse. This is what ``make ingest`` and the Airflow task call.
"""

from __future__ import annotations

import logging
import sys

from .config import PostgresConfig, StorageConfig
from .load_raw import run
from .storage import get_storage


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    storage_cfg = StorageConfig.from_env()
    pg_cfg = PostgresConfig.from_env()
    storage = get_storage(storage_cfg)
    logging.getLogger("ingestion").info(
        "extract+load: backend=%s schema=%s", storage_cfg.backend, pg_cfg.raw_schema
    )
    counts = run(pg_cfg, storage)
    logging.getLogger("ingestion").info("done: %s", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
