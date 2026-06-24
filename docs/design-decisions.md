# Design decisions

Lightweight ADR log — one entry per non-obvious architectural decision, terse and
decision-oriented. Newest first. "Accepted (pending)" means decided but not yet
implemented; the implementation checklist is the spec for the later execution batch.

---

## ADR-006 — Regenerate the model-pricing seed from `portkey-ai/models` (refresh-to-seed, not live fetch)

- **Status:** Accepted (pending) — its own execution batch. Independent of ADR-003/004.
- **Date:** 2026-06-24

### Context

Cost (`generator_cost_usd`/`writer_cost_usd`/`total_cost_usd`) is a headline metric of the
repo — "which retriever wins at what cost." Prices come from the **hand-maintained**
`dbt/seeds/seed_model_pricing.csv` (columns: `provider, model_resolved,
input_usd_per_mtok, output_usd_per_mtok, cache_read_usd_per_mtok, cache_write_usd_per_mtok,
effective_date, source_note`), joined in `int_scored_answers_enriched`:

```
cost = Σ(tokens × *_usd_per_mtok) / 1e6     -- generator joins on generator_model_resolved,
                                            -- writer joins on writer_model
```

This is fine at today's handful of models but doesn't scale, goes stale silently, and every
unpriced model yields NULL cost (honest, but lost signal). The pricing data is **not
benchmark-produced** — it's an independent reference the consumer owns — so improving its
*source* doesn't touch the producer/consumer boundary.

**Candidate source (verified 2026-06-24):** **`portkey-ai/models`**
(https://github.com/portkey-ai/models) — MIT-licensed open catalog, ~2000+ models / 40+
providers, per-provider JSON under `pricing/`, plus a free no-auth API
`https://configs.portkey.ai/pricing/{provider}.json`. Schema carries request/response token
prices + cache operations — a direct mapping onto the seed's four price columns.

### Decision

1. **Refresh-to-seed, *not* live fetch in the pipeline.** Golden rule #4 (local must run
   fully offline + reproducibly) forbids a network call on the build critical path. So a
   **standalone refresh script** pulls Portkey → regenerates the **committed**
   `seed_model_pricing.csv`; the pipeline keeps consuming a committed seed exactly as today.
   The seed stays the swap point — `int_scored_answers_enriched`'s join and the fact's cost
   columns are **unchanged**. Refresh runs out of band (on demand / scheduled), never inside
   `make pipeline`.
2. **Unit conversion in the script.** Portkey states prices in **cents per token**; the seed
   is **USD per million tokens**. Convert on write: `usd_per_mtok = cents_per_token × 10⁴`.
   Do not change the seed schema or the `/1e6` cost math — keep the conversion at the source
   boundary so the rest of the model is untouched.
3. **The real work is identity matching, not fetching.** The join keys on the *resolved*
   model string (`generator_model_resolved` / `writer_model`). NULLs arise from (a) no Portkey
   entry, or (b) a namespace mismatch between Portkey's model ids and the benchmark's resolved
   snapshot strings (`claude-haiku-4-5-20251001`, `qwen2.5:3b-instruct`). Reuse ADR-003's
   `macros/model_family.sql` (strip the `-YYYYMMDD` suffix) for snapshot normalization; keep a
   small hand-maintained alias map only for names Portkey doesn't carry verbatim (e.g. Ollama
   tags / local models, which are genuinely zero-cost).
4. **Preserve the honest-null invariant.** This refactor changes the *provenance* of prices,
   never the null semantics: an unmatched/unpriced model still yields **NULL** cost — never
   fabricated, never coerced to 0. Genuine local-zero (Ollama) stays an explicit `0.00` row
   with a `source_note`, as today. `source_note` becomes provenance: `portkey-ai/models @
   <commit-or-fetch-date>`.
5. **Cadence is a later increment, on the same script.** Default now = snapshot-on-demand
   (regenerate + commit the CSV — reproducible, diff-able in git). The "dynamic price
   ingestion" idea is just scheduling that script; it does **not** justify moving the fetch
   onto the pipeline path.

### Consequences

- (+) Removes hand-maintenance; widens coverage → fewer NULL costs; git-diff-able provenance
  (commit/date) instead of hand-written notes.
- (+) **Offline reproducibility preserved** — the pipeline's input is still a committed seed;
  no new runtime network dependency.
- (−) New refresh script + a dependency on Portkey's (community-maintained) JSON schema —
  treat it as an external contract: pin a commit/snapshot, validate the shape on refresh,
  tolerate-but-flag unknown providers.
- (−) Identity matching is ongoing: an alias map may still need occasional hand edits for
  models Portkey doesn't name verbatim. Smaller surface than pricing every model by hand.

### Implementation checklist (the execution plan)

_Refresh script (out-of-band; the only new moving part)_
- [ ] `ingestion/refresh_pricing.py` + `make refresh-pricing`: fetch
      `configs.portkey.ai/pricing/{provider}.json` for the providers in use; **convert
      cents/token → USD/Mtok (×10⁴)**; map Portkey model ids → `model_resolved`; write
      `dbt/seeds/seed_model_pricing.csv`; stamp `source_note = portkey-ai/models @ <commit|date>`.
- [ ] Pin the Portkey source (commit SHA or captured snapshot) so a refresh is reproducible;
      validate fetched JSON shape before writing (fail loud on schema drift).

_Identity matching_
- [ ] Reuse `macros/model_family.sql` (ADR-003) for `-YYYYMMDD` snapshot normalization; add a
      tiny alias seed for non-Portkey names (Ollama tags / local). Unmatched → omit → NULL cost.

_dbt (no model changes — the swap point holds)_
- [ ] `_seeds.yml`: mark the seed **generated** (don't hand-edit); document the refresh command,
      the unit (USD/Mtok), and the provenance `source_note`.
- [ ] No change to `int_scored_answers_enriched` cost math or the fact's cost columns. Re-run
      `make dbt`; spot-check previously-NULL costs now populate where Portkey covers them, and
      Ollama stays explicit-zero. Honest-null path still produces NULL for the genuinely unmatched.

_Docs_
- [ ] README cost section + `.claude/CLAUDE.md` cost note: the pricing seed is **regenerated
      from `portkey-ai/models`**; the offline pipeline consumes the committed seed unchanged.

---

## ADR-005 — Incremental build (deferred): keep full-rebuild until a load-shape trigger fires

- **Status:** Deferred (trigger-gated) — *not* implemented; record the decision + the signals
  that flip it. No execution batch until a trigger below is actually observed.
- **Date:** 2026-06-24

### Context

Today nothing is incrementally materialized, and "additive" data is a property of **two
layers that both do full work**:

- **Raw EL (`ingestion/load_raw.py`).** Tables are `CREATE TABLE IF NOT EXISTS`, so they
  persist and rows accumulate. Each run is landed by an **idempotent delete-replace keyed by
  `run_id`** (`DELETE … WHERE run_id = %s` then re-`INSERT`, in one transaction — whole-or-
  nothing). But `run()` loops `for run_id in storage.list_run_ids()` and **re-lands every
  discovered run on every pass** — O(all runs) of S3 reads + `executemany` inserts, even when
  only one run is new.
- **dbt (`dbt_project.yml`).** staging = `view`, intermediate = `ephemeral`, marts =
  **`table`** → the whole star is **dropped and recomputed from all raw rows on every
  `dbt build`**. New runs show up because raw grew, not because anything merged.

This is the **correct** default at current scale: the fact grain is run × question ×
condition (low-frequency benchmark runs, thousands of rows), surrogate keys are
hash-deterministic over fixed column lists (`surrogate_key([...])` in both fact and dim), so a
full rebuild is cheap *and* structurally can't produce duplicate/`*_sk`-drifted rows — the
classic failure mode of hand-rolled incremental merges. Incremental materialization buys
nothing until rebuild time or re-land cost actually hurts, and it carries a real hazard against
this repo's contract (below). So: **decide now to stay full-rebuild, and write down the
signals that would justify revisiting** — rather than rediscover them under load.

### Decision

1. **Stay full-rebuild** (marts `table`, raw re-land-all) until a trigger in the next section
   is observed in practice. Do not pre-optimize.
2. When a trigger fires, incrementality is added **at the layer the trigger points to** — the
   two are independent and need not move together:
   - **EL-side:** stop re-landing unchanged runs. Track per-run provenance in
     `raw.run_manifest` (a payload/content hash or the source object's ETag/`loaded_at`) and
     skip `run_id`s already present and unchanged; land only new/changed runs.
   - **dbt-side:** `fct_scored_answer` → `materialized='incremental'`,
     `incremental_strategy='delete+insert'`, **`unique_key='run_id'`** (replace a whole run's
     slice — mirrors raw's semantics exactly), with
     `{% if is_incremental() %} where run_id not in (select run_id from {{ this }}) {% endif %}`.
     `run_id` is the natural batch key because a run lands transactionally whole-or-nothing.
3. **Invariants any incremental implementation must preserve** (these are *why* it's risky, and
   the acceptance criteria when it lands):
   - **Surrogate-key determinism.** `delete+insert` keyed on `run_id` keeps the no-dup/no-drift
     guarantee full-rebuild gives for free, *because a run is replaced as a unit*. Keying on
     `scored_answer_sk` instead would not — avoid it.
   - **The append-only source contract (golden rule #1).** `run.json` is versioned and **may
     add keys**; a new mechanism adds new measures. dbt incremental's default
     `on_schema_change: ignore` would **silently swallow exactly those new columns** — directly
     against the repo's "tolerate unknown keys / schema-on-read" thesis. An incremental fact
     must set `on_schema_change: append_new_columns` (or `sync_all_columns`) and stay reconciled
     with the enforced `_marts.yml` contract.
   - **Re-runnable / idempotent transforms.** A `--full-refresh` path must remain the source of
     truth; incremental is an optimization over it, never a replacement.

### Triggering needs (the reminder — grounded in this repo's schema + shape)

Implement only when one of these is *observed*, not anticipated:

1. **The re-land-all loop dominates ingest.** `load_raw.run()` re-downloads + re-inserts every
   `run_id` each pass. Trigger: ingest wall-clock (or S3 GET/egress cost) grows roughly linearly
   with *total accumulated runs* rather than *new runs* — i.e. re-landing history is the bottleneck.
   → EL-side skip-unchanged. (First to bite, because it's O(all runs) on *every* run.)
2. **Marts full rebuild exceeds the orchestration window.** `+materialized: table` rebuilds the
   entire star each `dbt build`. The fact grain is run × question × condition — e.g. ~1k runs ×
   ~100 questions × 4 conditions ≈ 400k rows is still trivial; the bite is at ~10⁷+ fact rows,
   where the `traversal_info` explode + surrogate-key hashing + dim rebuilds make rebuild time
   the long pole of the DAG. → dbt-side incremental fact.
3. **Rebuild contends with live serving (ADR-001 hardware).** Cloud topology is a *single
   self-hosted Postgres container on a small `t4g` EC2 box, co-resident with Streamlit reading
   marts live.* Trigger: a full rebuild saturates that box (CPU/IO) and degrades dashboard query
   latency during the scheduled run — a contention signal, not a rowcount one, and it can fire at
   far smaller volumes than (2) because the box is shared and small. → dbt-side incremental fact
   (smaller write footprint per run).
4. **Ingest cadence rises.** Airflow is kept "for the skill, not because the cadence needs it"
   (CLAUDE.md). Trigger: runs start landing many times/day (continuous/CI-driven benchmarking)
   so re-land-all + full-rebuild repeats wastefully within a single day. → both layers.
5. **Runs get re-emitted / re-scored.** Today full rebuild absorbs a corrected run for free
   (raw's delete-replace + recompute). Trigger: the benchmark begins re-emitting an existing
   `run_id` (re-judge, score fix) frequently enough that targeted replacement matters. →
   confirms the `unique_key='run_id'` / `delete+insert` choice; the incremental merge must
   replace the run's whole slice, never append a second copy.

**Anti-trigger (do not implement for this):** "incremental is best practice" / "to show I know
it." Restraint is the signal here (CLAUDE.md working agreement). None of (1)–(5) is met at
current volume, so the answer stays full-rebuild.

### Consequences (when it lands)

- (+) Per-run ingest + build cost decouples from accumulated history.
- (−) Loses full-rebuild's free correctness guarantees; requires `on_schema_change` discipline
  against the append-only contract, a maintained `--full-refresh` path, and a `unique_key`
  matched to the `run_id` batch grain. Net new operational surface — which is the whole reason
  it's deferred until a trigger pays for it.

### Implementation sketch (spec for the eventual batch — not a checklist yet)

- **EL:** add a content hash / ETag column to `raw.run_manifest`; in `load_raw.run()` skip
  `run_id`s already present + unchanged; land only new/changed runs.
- **dbt:** `fct_scored_answer` → `config(materialized='incremental',
  incremental_strategy='delete+insert', unique_key='run_id',
  on_schema_change='append_new_columns')` + the `is_incremental()` `where run_id not in (…)`
  guard; keep `_marts.yml` contract reconciled; verify a `--full-refresh` reproduces a
  full-rebuild byte-for-byte (sk parity) before trusting the incremental path.

---

## ADR-004 — Enrich `dim_corpus` from the corpus-profile JSON (consume, don't re-measure)

- **Status:** Accepted (pending) — its own execution batch, independent of ADR-003.
- **Date:** 2026-06-24

### Context

`dim_corpus` today carries `node_count`, `edge_count`, `ttl_sha256` — all hardcoded `null`
(`dim_corpus.sql` casts them) — and models neither the vector-side counts (papers, chunks,
words) nor `triples`. So the star cannot answer "how big was the data under test," even
though the benchmark built a profile *specifically* for that.

The counts already exist upstream: `ingest/corpus_profile.py` measures a built corpus and
commits `ingest/corpus/<corpus_build_id>.json`, keyed by the same `corpus_build_id` the run
manifest stamps. Shape (verified against `full-2c102cb0.json` / `smoke-30c621e8.json`):
- `graph`: `triples`, `nodes`, `edges` (+ `ttl_bytes`/`ttl_sha256` provenance). **Null on
  smoke** — never loaded into GraphDB, so no endpoint to count against; the profile records
  null + a `source` note rather than a number measured against the wrong store.
- `vector`: `n_abstracts` (papers), `n_chunks`, `n_words` (source text size, overlap-free) +
  build config `embed_model`/`chunk_size`/`chunk_overlap` + `store_bytes`.

So this is **produce vs deliver**: nothing to measure upstream — the data exists. The work is
consumer-side ingestion + transform, plus a one-line *delivery* dependency.

### Decision

1. **Consume the profile, don't re-measure.** New raw source `raw.corpus_profile` → new
   `stg_corpus_profile` (flatten `graph.*`/`vector.*`, cast, rename) → `dim_corpus` joins it
   on `corpus_build_id` (already the grain). Spans ingestion → staging → marts — *not* staging
   alone, since staging only flattens what's already landed in `raw`.
2. **Columns (`*_count` convention, matching the dim's existing `node_count`/`edge_count`):**
   - graph: `triple_count`, `node_count`, `edge_count`
   - vector: `paper_count` (`n_abstracts`), `chunk_count` (`n_chunks`), `word_count` (`n_words`)
   - build knobs: `chunk_size`, `chunk_overlap`
   - provenance: `ttl_bytes`, `ttl_sha256`, `store_bytes`, `corpus_measured_at`
   - `embed_model` (from `vector.embed_model`): **lives here** (resolved 2026-06-24). It's folded
     into `corpus_build_id` (the build fingerprint hashes it), so it's FD on the corpus, not the
     retriever — removed from `dim_retriever_cond` in ADR-003, placed solely on `dim_corpus`. The
     dense retriever's embedding space is reached via the fact's `corpus_sk`.
3. **Honest nulls.** Graph counts stay null for smoke (no endpoint) — carry the null, never
   fabricate (same rule as cost-pricing). `dim_corpus` left-joins the profile so an unprofiled
   corpus still yields a row (counts null).

### Dependencies (the only upstream piece — delivery, not metadata)

- **CR to `biomedical-rag-bench`:** publish `ingest/corpus/<corpus_build_id>.json` to the run's
  object-storage landing prefix alongside `run.json`/`questions.jsonl`. CLAUDE.md already lists
  the corpus-profile JSON as a landed input, so this is the producer **honoring the existing
  contract** (a packaging change) — *not* a request to produce new metadata.
- **Boundary:** consume only via object storage; never read `../biomedical-rag-bench/ingest/corpus/*.json`
  from the pipeline. (Reading it to understand the shape is fine; wiring the pipeline to it is not.)
- **Local-first is not blocked:** seed the committed `smoke-*.json`/`full-*.json` as fixtures →
  the whole `raw → stg → dim_corpus` chain builds and validates offline now. The delivery CR
  gates only the cloud path.

### Consequences

- `dim_corpus` gains real size metrics → the dashboard can show corpus size and diff smoke vs
  full; `node_count`/`edge_count` stop being null placeholders (populated for full, null for
  smoke — honest).
- New raw source + staging model + ingestion code — a real surface increase, justified: the
  profile is a declared contract input, not a new dependency.
- Idempotent by `corpus_build_id` (content-addressed: a rebuilt corpus → new id → new row).
- Independent of ADR-003 (no shared file beyond `dim_corpus.sql`); can land before or after.

### Implementation checklist (the execution plan)

_Upstream (gates cloud only)_
- [ ] CR to the benchmark: publish `<corpus_build_id>.json` to the landing prefix.

_Fixtures (unblocks local now)_
- [ ] Add `smoke-*.json` + `full-*.json` profiles to `ingestion_sample/` and the MinIO seed.

_Ingestion_
- [ ] `ingestion/extract.py`: list + download corpus-profile JSON (its own key prefix).
- [ ] `ingestion/load_raw.py`: land into `raw.corpus_profile`, idempotent, keyed by `corpus_build_id`.

_dbt_
- [ ] `_sources.yml`: declare `raw.corpus_profile` (freshness optional — content-addressed, rarely changes).
- [ ] New `stg_corpus_profile.sql`: flatten `graph.*`/`vector.*` from JSONB, cast, rename to `*_count`.
- [ ] `dim_corpus.sql`: left-join `stg_corpus_profile` on `corpus_build_id`; replace the `cast(null …)` placeholders with the real columns.
- [ ] `_marts.yml`: document `dim_corpus`'s new columns; `not_null` on the always-present vector counts, leave graph counts nullable (smoke).

_Docs_
- [ ] README: add the profile JSON as a 4th landed input (source-contract section) and extend the `DIM_CORPUS` entity.
- [ ] CLAUDE.md: note `dim_corpus` enrichment from the profile (incl. `embed_model` now on `dim_corpus`, removed from `dim_retriever_cond`).

_Verify_
- [ ] `make pipeline`: confirm `dim_corpus` populates from fixtures (full = counts; smoke = null graph counts); dashboard shows corpus size.

---

## ADR-003 — Marts field-naming convention: knobs → dims, mechanism-prefixed measures

- **Status:** Accepted (pending) — implementation deferred to a later batch.
- **Date:** 2026-06-24

### Context

The fact accreted columns from the polymorphic `traversal_info` explosion with no
consistent rule, producing three classes of defect: (a) mechanism-specific measures
left bare while siblings were prefixed (`neighborhood_hops`/`sparql_num_rows` prefixed;
`num_triples`/`num_linked`/`num_chunks`/`top_k` not); (b) a condition *knob* (`top_k`)
modeled as a fact measure though it is fixed per condition; (c) overloaded stems where
two distinct concepts share a name (`hop_count` vs `neighborhood_hops`; the three "seed"
meanings — question seed-entities, `sampling_seed`, run seed; `total_tokens` that is
actually generator-only).

Verified against the producer (`biomedical-rag-bench`, read for semantics only — no code
coupling): `top_k` is a constructor knob — *"the analogue of the graph retriever's fan
caps"* (`retrievers/vector.py`); `num_chunks = len(ids)`, `num_triples = len(kept)`,
`num_linked = len(anchors)` are realized counts (`vector.py`/`graph.py`); `num_sources =
len(res.sources)` is computed by the harness for **every** retriever (`eval/run_eval.py`).
So `top_k` is categorically a setting; the four `num_*` are realized outcomes.

### Decision — the convention

1. **Measure vs knob.** Realized per-answer outcomes (counts, scores, tokens, latencies,
   costs) are fact measures. Settings fixed for the condition/run (`top_k`,
   `*_temperature`, `embed_model`, `writer_model`, `neighborhood_hops`) are **dimension
   attributes, never fact measures.**
2. **Prefixing.** Mechanism-specific measure → prefix with the *normalized mechanism*
   (`dense_`/`neighborhood_`/`sparql_`). Actor-attributable measure → prefix with the
   actor (`generator_`/`writer_`). Universal measure (every condition emits it) → **no
   prefix; the absence is the signal.** Prefix by **mechanism, not retriever** — retriever
   strings carry version suffixes (`graph_neighborhood%`), mechanism is the stable producer.
   A `total_*` column must be a genuine total of its unit; an actor-scoped sum is
   `<actor>_total_*` (so `generator_total_tokens`, never a bare `total_tokens` that hides
   the writer's). The `cost` trio (`generator_cost_usd`/`writer_cost_usd`/`total_cost_usd`)
   is the template tokens should match.
3. **Booleans** are `is_<predicate>` everywhere (`is_passed`, `is_error`, `is_sparql_valid`,
   `is_local`). One flag per concept — no parallel integer mirror of a boolean.
4. **Disambiguate overloaded stems** explicitly (`question_hop_count` vs
   `neighborhood_hops`; `num_seed_entities` vs sampling/run seeds).

### Decision — committed renames/moves (this batch)

The full broader-review set was promoted into scope on 2026-06-24 (nothing left pending).
Grouped by the convention rule each one serves.

**A. Mechanism-prefixed measures (stay in the fact):**
- `num_triples` → `neighborhood_num_triples`
- `num_linked`  → `neighborhood_num_linked`
- `num_chunks`  → `dense_num_chunks`

**B. Knobs → dimensions (off the fact entirely):**
- `top_k` → `dim_retriever_cond` (attribute **and grain**, parallel to `neighborhood_hops`).
- `embed_model` → **`dim_corpus`, not `dim_retriever_cond`** (resolved 2026-06-24). It's folded
  into `corpus_build_id` (the build fingerprint hashes the embed model), so it's functionally
  dependent on the *corpus*, not the retriever condition — the corpus pins the embedding space,
  the retriever can't pick another. Surfaced via **ADR-004** (from the profile's
  `vector.embed_model`); this batch just stops routing it to `dim_retriever_cond`. It stays
  parsed in `stg_traversal` as provenance.
- `writer_model` + `writer_temperature` → **new `dim_writer`** (grain = model × temperature),
  fact gains a `writer_sk` FK. *Not* `dim_retriever_cond` — its own comment rejects folding a
  volatile model string into that conformed dim; `dim_writer` mirrors `dim_generator` (the
  writer is a second LLM actor, not a retriever knob). `retriever_cond_sk` therefore gains
  `top_k` (not `embed_model`, not the writer).

**C. Actor-prefixed tokens (match the cost trio):**
- `input_tokens` → `generator_input_tokens`; `output_tokens` → `generator_output_tokens`
- `cache_read_input_tokens` → `generator_cache_read_tokens`; `cache_creation_input_tokens` → `generator_cache_creation_tokens`
- `total_tokens` → `generator_total_tokens` (it was generator-only — the name now tells the truth)
- `writer_tokens` → `writer_total_tokens` (symmetry with `generator_total_tokens`)
- `context_tokens_proxy` → **unchanged** (cross-actor context measure; the `_proxy` honesty suffix stays)

**D. Booleans (`is_<predicate>`):**
- `passed` → `is_passed`; `judged` → `is_judged`; `sparql_valid` → `is_sparql_valid`
- `is_error`, `is_local`, `is_graph` already conform.
- `is_pass` (integer mirror of `passed`) → **dropped**; pass-rate is `avg(is_passed::int)`.

**E. Disambiguation / placement:**
- `num_seeds` → `num_seed_entities` (`dim_question`; not a retriever field)
- `dim_question.hop_count` → `question_hop_count` (vs `neighborhood_hops`)
- `dim_question.answer_var` → **dropped from the star** (kept in `stg_questions` only — provenance, ~zero analytical value; never a meaningful slicer)
- `dim_judge` → **`dim_scoring`** (it's keyed on `scoring` with no judge model); `judge_sk` → `scoring_sk`
- `dim_run.judge` → `judge_model` (the run's actual judge identity)
- `dim_run.system_prompt_sha256` → `generator_system_prompt_sha256` (verified: it's the generator's answer system prompt — `SYSTEM_PROMPT` in `eval/harness.py`, "identical text for closed_book and every retriever"; no writer/judge prompt SHA exists in telemetry yet, so the actor prefix pre-empts that future ambiguity)
  - **Stays on `dim_run`, not `dim_generator` — and the actor prefix does not change that.** Name by *owner* (whose prompt), place by *grain* (what determines its value). The hash is a global harness constant: it does **not** co-vary with `(provider, model, temperature)`, so it is not functionally dependent on the `dim_generator` key — folding it into that key would split one generator into two rows on a prompt edit (a false identity). It *is* exactly one value per `run_id` (a manifest field), a **run-constant control factor** alongside `harness_version`/`corpus_build_id` — the benchmark itself calls the manifest "the factorial-provenance record" (`run_eval.py`). "Don't aggregate across prompts" is satisfied by it being a slice-able `dim_run` attribute (every dim attribute is a legal `GROUP BY`/filter), **not** by promoting it into a surrogate key — placement enforces nothing; the safeguard is including it in the grouping + surfacing it as a dashboard filter. Future `judge_*`/`writer_*` prompt SHAs: same FD test when they exist (a judge prompt determined by `scoring` would instead belong on `dim_scoring`).
- `num_sources` → **kept, unprefixed** (universal measure — the bare name is the signal)

**F. `dim_generator` conforming + model family (a data-quality fix folded into the same pass — `dim_generator` is already being rewritten):**
- Conform the generator identity on `generator_model_id = coalesce(generator_model_resolved, generator_model)` (**derived in staging, `stg_scored_answers` — not the dim**), used by **both** `dim_generator`'s grain and the fact's `generator_sk` key. Merges the null-`generator_model_resolved` rows where `generator_model` already carries the dated snapshot — the stray third `claude-haiku-4-5` row that today fragments the model into 3 dim rows (diagnosed against live data: a `(provider, null, null)` bucket whose `max(generator_model)` surfaces the snapshot string). The coalesce is generator-specific (only model field with a `_resolved`/alias pair).
- Add `generator_model_family` via a **reusable macro** `macros/model_family.sql` = `regexp_replace(<expr>, '-\d{8}$', '')` → e.g. `claude-haiku-4-5` — a **rollup label** for readable short-name filtering/grouping that unifies dated snapshots. FD on the identity → descriptive attribute, **not** part of the grain. Hierarchy: `generator_model_family` → `generator_model_id`. Supersedes the redundant `max(generator_model)` display label. Regex assumes the `-YYYYMMDD` snapshot suffix; date-less names (Ollama) pass through unchanged.
- **Row-level model normalization lives in staging, behind the macro**, so the *same processing* is the same code wherever a model string lands (`writer_model`, `embed_model`, `judge_model`). Define the macro once; add a `*_family` column only where it will actually be grouped/filtered — generator yes, the others on demand (restraint, not blanket). The dim's job stays cross-row grouping, not string-munging.
- **Honest limits (do not fabricate):** a run that logged only the bare alias with null `generator_model_resolved` cannot be merged to its snapshot without an alias→snapshot seed map; and the `temperature = 0.0` vs `null` split is a real factor (`null` = unpinned/provider default), kept distinct.

**G. Fact key hygiene — surrogate keys only, drop redundant columns:**
- Drop the **copied natural keys** `run_id`, `question_id` from `fct_scored_answer`. They duplicate `dim_run`/`dim_question` and are reachable via `run_sk`/`question_sk`; the fact keeps the surrogate PK `scored_answer_sk` + surrogate FKs only. **Convention:** every dim join is a hashed surrogate key built from the same column list in fact and dim (uniform single-column joins, including composite-key dims); natural/business keys live in their dimension, not the fact. Trade-off accepted: linking a fact row back to `raw` provenance now needs a dim join.
- Drop `neighborhood_hops` as a **fact measure** — it's a retriever knob already in `dim_retriever_cond`'s grain (reachable via `retriever_cond_sk`), so its fact column is redundant, exactly parallel to `top_k`. (Surfaced while regenerating the README ERD.) `writer_model` already leaves the fact via group B (`dim_writer`).

### Consequences

- The fact's **enforced contract** (`_marts.yml`, `contract.enforced`) changes substantially:
  ~14 column renames, drops (`top_k`, `writer_model`, `writer_temperature`,
  `is_pass`), and adds (`writer_sk`, plus FK rename `judge_sk`→`scoring_sk`). `dbt build`
  fails until the contract matches (the intended guardrail). The downstream consumer
  (the dashboard's direct-read column bindings) updates in lockstep.
- **`retriever_cond_sk` grain expands** (`+top_k`) — the **identical**
  `surrogate_key([...])` list must appear in both `fct` and `dim_retriever_cond` or the
  relationship test breaks. Full rebuild; **all sk values change**.
- **`generator_sk` re-keys on `generator_model_id`** (coalesced, derived in staging) in both
  `fct` and `dim_generator` → `generator_sk` values change; same full-rebuild caveat as above.
- The fact becomes **surrogate-keys-only**: it sheds `run_id`, `question_id`, `neighborhood_hops`
  (plus `writer_model`/`top_k`/`writer_temperature`/`is_pass` from earlier groups). Any query
  filtering the fact directly on `run_id`/`hops` must now join the relevant dim.
- **New `dim_writer`** adds a model + a `relationships` test; non-sparqlgen rows have a null
  writer → the dim must contain the null-writer member (built from observed combos, like
  `dim_generator`).
- **`dim_judge`→`dim_scoring`** is a model rename: `ref()`s, the `relationships` target, the
  fact FK column, and the `_marts.yml` block all move; CLAUDE.md's dim list updates.
- **Boolean changes touch the dashboard's pass-rate** (`is_pass` removed → `is_passed`).
- `CLAUDE.md` "Target model" + dim list: add `dim_writer`; `dim_judge`→`dim_scoring`; drop
  `top_k` from fact measures; reflect the token/boolean naming and `num_seed_entities`.

### Implementation checklist (the execution plan — staging → intermediate → marts → contract → docs → rebuild → dashboard)

_Macros_
- [ ] New `macros/model_family.sql`: `regexp_replace(<expr>, '-\d{8}$', '')` — strip the dated snapshot suffix (date-less names pass through). The single home for model-name normalization; reused wherever a `*_family` column is added.

_Staging_
- [ ] `stg_traversal.sql`: rename measures `num_triples`→`neighborhood_num_triples`, `num_linked`→`neighborhood_num_linked`, `num_chunks`→`dense_num_chunks`. Keep `top_k`/`writer_model`/`writer_temperature` routing to dims; `embed_model` stays parsed as **provenance only** (its star home is `dim_corpus` via ADR-004, from the corpus profile — not routed here). Apply `model_family()` to `writer_model` only if that rollup will be grouped/filtered (don't add unused).
- [ ] `stg_scored_answers.sql`: `input_tokens`→`generator_input_tokens`, `output_tokens`→`generator_output_tokens`, `cache_read_input_tokens`→`generator_cache_read_tokens`, `cache_creation_input_tokens`→`generator_cache_creation_tokens`; `passed`→`is_passed`, `judged`→`is_judged`; add `generator_model_id = coalesce(generator_model_resolved, generator_model)` and `generator_model_family = {{ model_family('coalesce(generator_model_resolved, generator_model)') }}`. (`num_sources`, `context_tokens_proxy`, `is_error` source unchanged.)
- [ ] `stg_questions.sql`: `num_seeds`→`num_seed_entities`; `hop_count`→`question_hop_count`.
- [ ] `stg_runs.sql`: `judge`→`judge_model`; `system_prompt_sha256`→`generator_system_prompt_sha256`.

_Intermediate_
- [ ] `int_scored_answers_enriched.sql`: propagate every rename above; drop the now-redundant `q.hop_count as question_hop_count` alias (source is already `question_hop_count`); compute `generator_total_tokens` (gen in+out) and `writer_total_tokens` (writer in+out); keep `top_k`/`writer_model`/`writer_temperature` flowing for the dims; pass through `generator_model_id` + `generator_model_family` for `dim_generator`.

_Marts — dims_
- [ ] New `dim_writer.sql`: grain (`writer_model`, `writer_temperature`); `writer_sk = surrogate_key(['writer_model','writer_temperature'])`; built from observed combos in `int_` (includes the null-writer member).
- [ ] `dim_generator.sql`: group by `(generator_provider, generator_model_id, generator_temperature)`; `generator_sk = surrogate_key(['generator_provider','generator_model_id','generator_temperature'])`; select `generator_model_family`; drop the redundant `max(generator_model)` display label (family replaces it).
- [ ] `dim_retriever_cond.sql`: add `top_k` to the `observed` group-by **and** the `surrogate_key([...])`; select it as an attribute. (`embed_model` is **not** added — it belongs to `dim_corpus`, ADR-004.)
- [ ] `dim_question.sql`: `num_seeds`→`num_seed_entities`; select `question_hop_count`; **drop `answer_var`** from the select (it stays in `stg_questions` for provenance).
- [ ] `dim_run.sql`: `judge`→`judge_model`; `system_prompt_sha256`→`generator_system_prompt_sha256`.
- [ ] Rename `dim_judge.sql`→`dim_scoring.sql`; `judge_sk`→`scoring_sk` (keep the `seed_scoring_labels` join).

_Marts — fact_
- [ ] `fct_scored_answer.sql`: apply all measure/boolean renames (group A, C, D); **remove** `run_id`, `question_id` (copied natural keys), `neighborhood_hops` (redundant knob, lives in `dim_retriever_cond` grain), `top_k`, `writer_model`, `writer_temperature`, `is_pass`; add `writer_sk = surrogate_key(['writer_model','writer_temperature'])`; add `'top_k'` to the `retriever_cond_sk` key list (must match `dim_retriever_cond` exactly); rename FK `judge_sk`→`scoring_sk`; recompute `generator_sk` on `['generator_provider','generator_model_id','generator_temperature']` to match `dim_generator`.

_Contract + tests_
- [ ] `_marts.yml`: fact contract — every rename, the drops, add `writer_sk` (+ `relationships`→`dim_writer`), rename `judge_sk`→`scoring_sk` (+ retarget relationship); rename the `dim_judge` model block → `dim_scoring`; update `dim_question` `accepted_values` to `question_hop_count`; note `top_k` in the `dim_retriever_cond` grain.

_Docs_
- [ ] `.claude/CLAUDE.md` "Target model" + dim list: add `dim_writer`; `dim_judge`→`dim_scoring`; drop `top_k` from fact measures (now a retriever-cond attr); reflect token/boolean naming + `num_seed_entities`; note `dim_generator` conformed on `coalesce(model_resolved, model)` + the `generator_model_family` rollup (via the `model_family` macro).

_Rebuild + verify_
- [ ] `make parse` → `make dbt`: full rebuild (sk values change); confirm contract + all relationships (incl. new `writer_sk`, renamed `scoring_sk`) pass; spot-check the `fct`↔`dim_retriever_cond` join after the grain expansion.

_Dashboard_
- [ ] Sweep `dashboard/` for every renamed/removed column; switch pass-rate to `avg(is_passed::int)` (was `is_pass`); update any `dim_judge`/`judge_sk` references. (The `dbt/analyses/*` probes reference tables, not columns — unaffected.)

---

## ADR-002 — Cosmos renders dbt models as individual Airflow tasks

- **Status:** Accepted — implemented.
- **Date:** 2026-06-19

### Context

The Airflow DAG ran `dbt build` as a single `BashOperator`. Failures showed one red
box — no visibility into which model or test broke. Cosmos (`astronomer-cosmos`)
parses the dbt project and renders each model/seed/test as its own Airflow task with
correct dependency edges.

### Decision

1. Replace the `BashOperator` dbt step with a `DbtTaskGroup` from Cosmos.
2. dbt is installed in an **isolated virtualenv** inside the Airflow container (avoids
   dependency conflicts). Cosmos uses `ExecutionConfig(dbt_executable_path=...)` to
   call it.
3. Profile config reuses the same `profiles.yml` as `make dbt` — target driven by
   `DBT_TARGET` env var (CLAUDE.md rule #3).
4. The Airflow image is built from `airflow/Dockerfile` (extends the stock image with
   cosmos + dbt-postgres).
5. **Version pinning:** `dbt-postgres` and `astronomer-cosmos` are pinned in both
   `pyproject.toml` (IDE/local) and `airflow/Dockerfile` (runtime). A CI check should
   verify the two stay in sync — not yet implemented.

### Consequences

- (+) Full dbt DAG visible in the Airflow UI; failures pinpoint the exact model/test.
- (+) Same profiles.yml and target mechanism as the local path.
- (−) Dockerfile build is no longer a stock pull — adds ~30s to the first build.
- (−) Duplicate version pins across pyproject.toml and Dockerfile until the CI check
  is added.

### Pending

- [ ] CI check: verify pinned versions in `pyproject.toml` match `airflow/Dockerfile`.

---

## ADR-001 — Self-hosted warehouse + dashboard, direct connection (supersedes the RDS / Community-Cloud defaults)

- **Status:** Accepted — **local serving half implemented 2026-06-25** (direct-connect
  dashboard + read-only role; Parquet export removed). Cloud infra (RDS→EC2 container,
  security groups, in-VPC topology) still deferred to the cloud milestone.
- **Date:** 2026-06-19 (amended 2026-06-25)

### Amendment (2026-06-25) — export removed entirely, not gated behind a swap point

Decisions #3/#4 below kept Parquet/S3 export as an *optional* serving path behind a
`SERVE_MODE=postgres|parquet` swap point. **Superseded:** the export path is **removed
outright** — `serve/export_marts.py`, the `make export` target, the Airflow `export_marts`
task, the CI export step, the marts S3 bucket (compose `minio-init`, `seed_storage`,
`StorageConfig`, Terraform), and the `S3_MARTS_*` env are all deleted. Rationale: the user
does not want an intermediate Parquet stage as a validation point, and a `SERVE_MODE` switch
with only one live implementation is dead code carrying false optionality. Streamlit-
Community-Cloud-from-Parquet stays a **documented, re-addable** fallback (it would
reintroduce an exporter + a `parquet` reader), but is no longer maintained code.

Implemented this batch: the dashboard (`dashboard/app.py`) reads `<DBT_SCHEMA>_marts`
directly as a least-privilege `marts_reader` role, provisioned idempotently by a dbt
`on-run-end` hook (`dbt/macros/grant_marts_reader.sql`) — re-applied each build because
marts are `table`-materialized (dropped/recreated). Verified: `make pipeline` green
(PASS=71 incl. the grant hook); `marts_reader` reads marts but is denied on raw/staging and
on writes. Still deferred (cloud milestone): the RDS→EC2 container swap, security groups,
and the in-VPC topology; the optional `streamlit` compose service (local uses host-run
`make dashboard`).

### Context

The data is small (thousands of rows), so Postgres is more than enough as the dbt
transformation engine; no MPP warehouse is warranted. The original CLAUDE.md cost
defaults were **RDS `db.t4g.micro`** + **Streamlit Community Cloud** reading **Parquet
exported to S3** — that combo existed to keep the warehouse private while using *free*
SaaS hosting that lives outside the VPC (so it can't reach a private DB; hence the
file handoff).

We instead want a **fully self-managed stack** (cost/control, and it demonstrates the
end-to-end skill). Self-hosting Streamlit **inside the VPC** removes the only reason for
the Parquet handoff: the dashboard can reach the warehouse privately over the network.

### Decision

1. **Warehouse hosting:** Postgres in a **container, self-hosted on EC2** — *not* RDS.
   Same engine local (docker-compose) and cloud (container on EC2), so dbt models are
   unchanged across environments.
2. **Serving:** **self-hosted Streamlit**, in the **same VPC** as the warehouse,
   connecting **directly to the `*_marts` schema** over a private security-group rule.
   The DB gets **no public ingress**; only the dashboard's own port is exposed.
3. **Parquet/S3 export becomes optional**, kept behind a swap point — not the primary
   serving path. Retain it only if we also want a Community-Cloud fallback or archived
   snapshots.
4. **Dashboard data access behind one swap point:** `load_mart()` in `dashboard/app.py`
   gets two implementations — `postgres` (direct SQL on marts) and `parquet` (read from
   object storage) — selected by `SERVE_MODE` env (default `postgres` for self-hosted).
5. **Marts-only rule preserved:** the dashboard reads the `*_marts` schema (or read-only
   views over it) via a **read-only DB role**, never `raw`/`staging`/`intermediate`.

### Environments

- **dev:** docker-compose on the laptop (postgres + streamlit). Direct `localhost` connection.
- **staging:** single EC2 box running the Postgres container + Streamlit container (+
  ingestion/dbt as a scheduled task). Shared failure domain — acceptable for staging.
- **prod:** separate the concerns — dedicated box (or instance) for Postgres vs. the app;
  exact prod topology TBD. RDS remains a fallback if Postgres ops burden proves too high.

### Consequences

- (+) Full control, no managed-service lock-in, live data (no export lag), fewer moving
  parts in the serve path, and a complete self-managed-stack skill signal.
- (−) **We own Postgres ops:** backups, patching, durability, hardening. Mitigate with
  volume snapshots, a pinned image, a restricted SG, and no public DB port.
- (−) The DB must be up whenever the dashboard is used — **not idle-to-zero** like the
  Parquet/Community-Cloud path. Acceptable since the same EC2 already hosts both.
- (−) Lose the free hosting tier; we pay for the EC2 box(es).
- (−) Single box in dev/staging is a shared failure domain; prod must separate at least
  the DB from the app.

### Implementation checklist (for the execution batch)

_Done 2026-06-25 (local serving half) — see the Amendment:_
- [x] `dashboard/app.py`: `load_mart` reads `<DBT_SCHEMA>_marts` directly via `psycopg`.
      (No `SERVE_MODE` switch — direct-only; the parquet branch was the dropped option.)
- [x] `.env.example`: dashboard read-only creds (`MARTS_READER_USER/PASSWORD`); marts schema
      derived from `DBT_SCHEMA`. (No `SERVE_MODE` — only one serving path now.)
- [x] dbt / SQL: **read-only role** `marts_reader`, `SELECT` on the marts schema only,
      provisioned by the `grant_marts_reader` on-run-end hook (least privilege).
- [x] `.claude/CLAUDE.md`: Environments + Cost + rule #2 make direct-connect the primary
      path; Parquet/Community-Cloud demoted to a documented (unbuilt) fallback.
- [x] `README.md`: architecture/serving narrative + cost wording updated.
- [x] `Makefile`: `make dashboard` is direct-connect; `export` target removed.

_Deferred to the cloud milestone:_
- [ ] `docker-compose.yml`: optional `streamlit` service (dev). Local uses host-run
      `make dashboard` — not blocking.
- [ ] `infra/`: replace the RDS resource with **EC2 + containerized Postgres** + security
      groups — DB SG allows the app SG + the in-VPC dashboard SG only; app SG exposes the
      dashboard port to an IP allowlist or an authenticated ALB. (Marts S3 bucket already
      removed — no Parquet fallback to retain.)

### Security note

Dashboard connects with a **read-only role** scoped to marts; the DB never gets a public
ingress rule; the app box exposes only the dashboard port, behind an IP allowlist or an
authenticated ALB.
