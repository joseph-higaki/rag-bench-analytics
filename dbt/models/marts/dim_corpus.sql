-- Corpus dimension. Grain: one row per corpus_build_id ('full-2c102cb0' => scale + short
-- sha, parsed out here). Size metrics (graph/vector counts, embed_model, provenance) come
-- from the corpus-profile JSON (ADR-004), left-joined on corpus_build_id.
--
-- Driver = union of corpora *observed in runs* and corpora *we have a profile for*:
--   - keeps the null-corpus member (older runs carry no corpus_build_id) so the fact's
--     corpus_sk relationship test holds;
--   - surfaces a profiled-but-unreferenced corpus (e.g. smoke) so the dim is a real catalog.
-- Counts are null for any member without a profile (older/unprofiled) — honest, not fabricated;
-- graph counts are additionally null for smoke (no endpoint to count against).
with observed as (
    select distinct corpus_build_id from {{ ref('stg_runs') }}
    union
    select distinct corpus_build_id from {{ ref('stg_corpus_profile') }}
),
profile as (
    select * from {{ ref('stg_corpus_profile') }}
)
select
    {{ surrogate_key(['o.corpus_build_id']) }}                          as corpus_sk,
    o.corpus_build_id,
    coalesce(p.corpus_scale, split_part(coalesce(o.corpus_build_id, ''), '-', 1)) as corpus_scale,
    split_part(coalesce(o.corpus_build_id, ''), '-', 2)                as corpus_sha,
    p.triple_count,
    p.node_count,
    p.edge_count,
    p.paper_count,
    p.chunk_count,
    p.word_count,
    p.chunk_size,
    p.chunk_overlap,
    p.ttl_bytes,
    p.ttl_sha256,
    p.store_bytes,
    p.embed_model,
    p.corpus_measured_at
from observed o
left join profile p on o.corpus_build_id = p.corpus_build_id
