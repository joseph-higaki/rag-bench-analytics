"""Unit tests for the storage layer — run against the committed fixtures, no DB needed."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingestion.storage import LocalStorage

FIXTURES = Path(__file__).resolve().parents[1] / "ingestion_sample"


@pytest.fixture(scope="module")
def storage() -> LocalStorage:
    return LocalStorage(FIXTURES)


def test_discovers_runs(storage: LocalStorage):
    runs = storage.list_run_ids()
    assert len(runs) > 0
    # Discovery is by manifest file; every discovered run must have a readable manifest.
    for run_id in runs:
        manifest = storage.read_manifest(run_id)
        assert manifest["run_id"] == run_id


def test_questions_present_and_cover_records(storage: LocalStorage):
    assert storage.has_questions()
    question_ids = {q["question_id"] for q in storage.read_questions()}
    assert question_ids, "no questions parsed"
    # Every record's question_id must exist in the question bank (the transform join key).
    for run_id in storage.list_run_ids():
        for rec in storage.read_records(run_id):
            assert rec["question_id"] in question_ids


def test_every_record_parses_and_has_grain_keys(storage: LocalStorage):
    total = 0
    for run_id in storage.list_run_ids():
        for rec in storage.read_records(run_id):
            total += 1
            assert rec.get("question_id"), "record missing question_id (grain key)"
            assert rec.get("retriever"), "record missing retriever (condition key)"
    assert total > 100, "expected the full fixture set"


def test_traversal_info_polymorphism_is_tolerated(storage: LocalStorage):
    """The riskiest part: traversal_info varies and is sometimes empty. Ensure the
    mechanism is derivable for EVERY record from explicit key or top-level retriever —
    mirrors the COALESCE logic in stg_traversal.sql."""
    def derive(rec: dict) -> str | None:
        ti = rec.get("traversal_info") or {}
        mech = ti.get("mechanism")
        if mech:
            return mech
        r = rec.get("retriever", "")
        if r == "vector":
            return "dense"
        if r.startswith("graph_neighborhood"):
            return "neighborhood"
        if r == "graph_sparqlgen":
            return "sparqlgen"
        if r == "closed_book":
            return "none"
        return None

    valid = {"dense", "neighborhood", "sparqlgen", "none"}
    for run_id in storage.list_run_ids():
        for rec in storage.read_records(run_id):
            assert derive(rec) in valid, rec.get("retriever")


def test_discovers_corpus_profiles(storage: LocalStorage):
    """Corpus profiles live in the reference/ subdir, keyed by corpus_build_id (the filename)."""
    ids = storage.list_corpus_build_ids()
    assert ids, "no corpus profiles discovered"
    for corpus_build_id in ids:
        profile = storage.read_corpus_profile(corpus_build_id)
        assert profile["corpus_build_id"] == corpus_build_id
        # Vector counts are always emitted; graph counts may be null (smoke has no endpoint).
        assert profile["vector"]["n_chunks"] is not None


def test_missing_dir_raises():
    with pytest.raises(FileNotFoundError):
        LocalStorage("/nonexistent/path/xyz")
