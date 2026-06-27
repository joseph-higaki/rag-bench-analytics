"""Land run files into the ``raw`` schema as-is (JSONB). No transformation here.

Idempotent and keyed by ``run_id``: re-loading a run deletes and replaces its rows
inside a single transaction, so the pipeline is safely re-runnable (CLAUDE.md golden
rule: idempotent loads keyed by run_id). The benchmark contract is append-only and may
add keys, so we never enumerate fields — we keep the whole record in a JSONB column and
let dbt staging do schema-on-read flattening.
"""

from __future__ import annotations

import logging

import psycopg
from psycopg.types.json import Json

from .config import PostgresConfig
from .storage import Storage

log = logging.getLogger("ingestion.load_raw")

# Tolerate unknown keys: the payload is stored whole; typed columns are derived later.
DDL = """
CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.run_manifest (
    run_id      text PRIMARY KEY,
    source_uri  text NOT NULL,
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    payload     jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS {schema}.scored_answer (
    run_id      text NOT NULL,
    question_id text NOT NULL,
    line_no     integer NOT NULL,
    source_uri  text NOT NULL,
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    payload     jsonb NOT NULL,
    PRIMARY KEY (run_id, question_id)
);

CREATE TABLE IF NOT EXISTS {schema}.question (
    question_id text PRIMARY KEY,
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    payload     jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS {schema}.corpus_profile (
    corpus_build_id text PRIMARY KEY,
    source_uri      text NOT NULL,
    loaded_at       timestamptz NOT NULL DEFAULT now(),
    payload         jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS {schema}.model_pricing (
    provider    text PRIMARY KEY,
    source_uri  text NOT NULL,
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    payload     jsonb NOT NULL
);
"""


def ensure_schema(conn: psycopg.Connection, schema: str) -> None:
    conn.execute(DDL.format(schema=schema))


def load_run(conn: psycopg.Connection, storage: Storage, schema: str, run_id: str) -> int:
    """Replace one run's raw rows. Returns the number of scored-answer records loaded."""
    manifest = storage.read_manifest(run_id)
    source_uri = storage.source_uri(run_id)
    records = list(storage.read_records(run_id))

    with conn.transaction():  # all-or-nothing: a failed run never half-lands
        conn.execute(f"DELETE FROM {schema}.scored_answer WHERE run_id = %s", (run_id,))
        conn.execute(f"DELETE FROM {schema}.run_manifest WHERE run_id = %s", (run_id,))

        conn.execute(
            f"INSERT INTO {schema}.run_manifest (run_id, source_uri, payload) "
            f"VALUES (%s, %s, %s)",
            (run_id, source_uri, Json(manifest)),
        )
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {schema}.scored_answer "
                f"(run_id, question_id, line_no, source_uri, payload) "
                f"VALUES (%s, %s, %s, %s, %s)",
                [
                    (run_id, rec.get("question_id"), i, source_uri, Json(rec))
                    for i, rec in enumerate(records)
                ],
            )
    log.info("loaded run %s (%d records)", run_id, len(records))
    return len(records)


def load_questions(conn: psycopg.Connection, storage: Storage, schema: str) -> int:
    """Full-refresh the shared question bank (it is small and not run-scoped)."""
    if not storage.has_questions():
        log.warning("no questions.jsonl in source; skipping question load")
        return 0
    questions = list(storage.read_questions())
    with conn.transaction():
        conn.execute(f"TRUNCATE {schema}.question")
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {schema}.question (question_id, payload) VALUES (%s, %s)",
                [(q.get("question_id"), Json(q)) for q in questions],
            )
    log.info("loaded %d questions", len(questions))
    return len(questions)


def load_corpus_profiles(conn: psycopg.Connection, storage: Storage, schema: str) -> int:
    """Land every corpus profile. Idempotent upsert keyed by corpus_build_id (the build is
    content-addressed, so the id changes when content changes — collisions are pure re-loads)."""
    count = 0
    for corpus_build_id in storage.list_corpus_build_ids():
        profile = storage.read_corpus_profile(corpus_build_id)
        source_uri = storage.corpus_source_uri(corpus_build_id)
        conn.execute(
            f"INSERT INTO {schema}.corpus_profile (corpus_build_id, source_uri, payload) "
            f"VALUES (%s, %s, %s) "
            f"ON CONFLICT (corpus_build_id) DO UPDATE SET "
            f"source_uri = EXCLUDED.source_uri, payload = EXCLUDED.payload, loaded_at = now()",
            (corpus_build_id, source_uri, Json(profile)),
        )
        count += 1
    log.info("loaded %d corpus profiles", count)
    return count


def load_pricing_snapshots(conn: psycopg.Connection, storage: Storage, schema: str) -> int:
    """Land each external model-pricing snapshot (e.g. Portkey pricing/<provider>.json), keyed by
    provider. Idempotent upsert: a refreshed snapshot replaces the provider's row. This is the first
    *non-benchmark* reference input — stored whole (schema-on-read), then flattened + unit-converted
    (cents/token -> usd_per_mtok) in stg_model_pricing_portkey."""
    count = 0
    for provider in storage.list_pricing_providers():
        payload = storage.read_pricing_snapshot(provider)
        source_uri = storage.pricing_source_uri(provider)
        conn.execute(
            f"INSERT INTO {schema}.model_pricing (provider, source_uri, payload) "
            f"VALUES (%s, %s, %s) "
            f"ON CONFLICT (provider) DO UPDATE SET "
            f"source_uri = EXCLUDED.source_uri, payload = EXCLUDED.payload, loaded_at = now()",
            (provider, source_uri, Json(payload)),
        )
        count += 1
    log.info("loaded %d model-pricing snapshots", count)
    return count


def run(cfg: PostgresConfig, storage: Storage) -> dict[str, int]:
    """Land questions + corpus profiles + pricing snapshots + every discovered run. Returns counts
    for logging/tests."""
    counts = {"runs": 0, "records": 0, "questions": 0, "corpus_profiles": 0, "pricing_snapshots": 0}
    with psycopg.connect(cfg.conninfo, autocommit=True) as conn:
        ensure_schema(conn, cfg.raw_schema)
        counts["questions"] = load_questions(conn, storage, cfg.raw_schema)
        counts["corpus_profiles"] = load_corpus_profiles(conn, storage, cfg.raw_schema)
        counts["pricing_snapshots"] = load_pricing_snapshots(conn, storage, cfg.raw_schema)
        for run_id in storage.list_run_ids():
            counts["records"] += load_run(conn, storage, cfg.raw_schema, run_id)
            counts["runs"] += 1
    return counts
