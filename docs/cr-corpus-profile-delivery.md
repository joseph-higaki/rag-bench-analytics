# CR: publish the corpus-profile JSON to the run's object-storage landing prefix

> Draft issue text to file against **`biomedical-rag-bench`** (the producer). Origin:
> rag-bench-analytics ADR-004. This is a packaging/delivery change — the producer
> **honoring an existing contract input** — not a request to produce new metadata.

---

**Title:** Publish `ingest/corpus/<corpus_build_id>.json` to the run landing prefix alongside `run.json`/`questions.jsonl`

**Labels:** `contract`, `packaging`, `consumer:rag-bench-analytics`

## Summary

When a run is published to the object-storage landing zone, the corpus-profile JSON
for that run's `corpus_build_id` is **not** uploaded. The profile is already measured
and committed in-repo (`ingest/corpus/<corpus_build_id>.json`); it just never leaves
the producer. Please publish it to the landing zone so downstream consumers can read
it from storage instead of reaching into this repo.

This is **delivery only** — the data already exists and the file already has a stable
shape. No new measurement, no schema change.

## Why this is in-contract, not a new ask

The corpus-profile JSON is already a declared landed input on the consumer side
(it sits beside `run.json` and `questions.jsonl` in the documented landing layout).
The producer measures it (`ingest/corpus_profile.py`) and commits it, but the publish
step omits it. So the contract already says the file should be in the landing zone —
this CR just makes the producer actually put it there. The consumer must **never**
read `../biomedical-rag-bench/ingest/corpus/*.json` directly; object storage is the
only sanctioned boundary, which is exactly why this gap blocks the cloud path.

## What exists today (no change needed)

- `ingest/corpus_profile.py` measures a built corpus and writes
  `ingest/corpus/<corpus_build_id>.json`.
- The run manifest already stamps the join key: `corpus_build_id` (e.g.
  `"corpus_build_id": "full-2c102cb0"`).
- Profile shape is stable and content-addressed by build id. Example
  (`smoke-30c621e8.json`):

  ```jsonc
  {
    "corpus_build_id": "smoke-30c621e8",
    "scale": "smoke",
    "measured_at": "2026-06-11T14:40:37+0200",
    "graph": {
      "ttl_path": "...", "ttl_bytes": 45594, "ttl_sha256": "98d458...",
      "endpoint": null, "triples": null, "nodes": null, "edges": null,
      "source": "ttl-provenance-only (no endpoint serving this corpus)"
    },
    "vector": {
      "chroma_path": "...", "collection": "pubmed_abstracts",
      "store_bytes": 774308, "n_chunks": 28, "n_abstracts": 15, "n_words": 3402,
      "abstracts_dir": "...", "embed_model": "sentence-transformers/all-MiniLM-L6-v2",
      "chunk_size": 180, "chunk_overlap": 30
    }
  }
  ```

## The ask

At publish time, upload the run's corpus-profile JSON to the landing bucket under the
shared reference prefix, keyed by `corpus_build_id`:

```
s3://<landing-bucket>/<reference-prefix>/<corpus_build_id>.json
```

For the reference consumer config that is (the `reference/` prefix also holds
`questions.jsonl` — both are shared, non-run-scoped inputs; ADR-007):

```
s3://rag-bench-landing/reference/full-2c102cb0.json
s3://rag-bench-landing/reference/smoke-30c621e8.json
```

Layout for context (run files vs shared reference inputs sit under different prefixes by
grain: profiles/questions are shared across runs, run files are per-run):

```
s3://rag-bench-landing/
├── runs/        <run_id>.jsonl + <run_id>.manifest.json   (per run; local batch dirs flatten here)
└── reference/   <corpus_build_id>.json + questions.jsonl  (shared across runs)
```

## Acceptance criteria

- [ ] Publishing a run uploads `ingest/corpus/<corpus_build_id>.json` (the id from the
      run manifest) to `<landing-bucket>/<reference-prefix>/<corpus_build_id>.json`.
- [ ] Object filename is exactly `<corpus_build_id>.json` — no timestamp, no run id.
- [ ] Upload is **idempotent / re-publish-safe**: many runs share one corpus build id;
      re-uploading the same id is a no-op or harmless overwrite (the id is
      content-addressed, so same id ⇒ same bytes).
- [ ] JSON shape unchanged from what's committed in-repo (top-level `corpus_build_id`,
      `scale`, `measured_at`; nested `graph.*` and `vector.*`).
- [ ] Honest nulls preserved: smoke-scale graph counts stay `null` with the `source`
      note (no endpoint to count against) — do **not** backfill or fabricate.
- [ ] The destination prefix is configurable (the consumer reads it via
      `S3_REFERENCE_PREFIX`, default `reference/`).

## Out of scope (explicitly not asked)

- No new fields, renames, or schema changes to the profile JSON.
- No re-measuring of corpora; graph counts on never-served corpora stay null.
- No changes to `run.json` / `questions.jsonl`.

## Impact if not done

The consumer's `dim_corpus` enrichment (size metrics, `embed_model`, chunk config)
works locally against seeded fixtures but cannot populate from real runs in the cloud
— `node_count`/`edge_count`/`triple_count` and the vector counts stay null in the
cloud warehouse. This CR is the sole upstream dependency gating the cloud path for
ADR-004.
