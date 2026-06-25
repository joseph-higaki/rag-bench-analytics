"""Integration test for the raw loader — requires a Postgres reachable via env.

Skipped automatically when POSTGRES_HOST is unset (so the unit suite runs anywhere).
CI sets these against a service container. Verifies idempotency: loading twice yields
the same row counts, keyed by run_id.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_HOST"),
    reason="no Postgres configured (set POSTGRES_HOST to run integration tests)",
)

FIXTURES = Path(__file__).resolve().parents[1] / "ingestion_sample"


@pytest.fixture()
def loaded():
    import psycopg

    from ingestion.config import PostgresConfig
    from ingestion.load_raw import run
    from ingestion.storage import LocalStorage

    cfg = PostgresConfig.from_env()
    storage = LocalStorage(FIXTURES)
    first = run(cfg, storage)
    second = run(cfg, storage)  # re-run must be idempotent
    with psycopg.connect(cfg.conninfo) as conn:
        manifests = conn.execute(
            f"select count(*) from {cfg.raw_schema}.run_manifest"
        ).fetchone()[0]
        records = conn.execute(
            f"select count(*) from {cfg.raw_schema}.scored_answer"
        ).fetchone()[0]
        corpus = conn.execute(
            f"select count(*) from {cfg.raw_schema}.corpus_profile"
        ).fetchone()[0]
    return first, second, manifests, records, corpus


def test_idempotent_load(loaded):
    first, second, manifests, records, corpus = loaded
    assert first == second, "second load changed counts — not idempotent"
    assert manifests == first["runs"], "manifest rows != runs loaded"
    assert records == first["records"], "scored_answer rows != records loaded (duplication?)"
    assert corpus == first["corpus_profiles"], "corpus_profile rows != profiles (upsert leak?)"
