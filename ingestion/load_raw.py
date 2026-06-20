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


def run(cfg: PostgresConfig, storage: Storage) -> dict[str, int]:
    """Land questions + every discovered run. Returns simple counts for logging/tests."""
    counts = {"runs": 0, "records": 0, "questions": 0}
    with psycopg.connect(cfg.conninfo, autocommit=True) as conn:
        ensure_schema(conn, cfg.raw_schema)
        counts["questions"] = load_questions(conn, storage, cfg.raw_schema)
        for run_id in storage.list_run_ids():
            counts["records"] += load_run(conn, storage, cfg.raw_schema, run_id)
            counts["runs"] += 1
    return counts
