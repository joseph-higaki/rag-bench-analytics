-- Flatten the corpus-profile payload (1:1 with raw.corpus_profile). Cast + rename only;
-- no joins. graph.* counts are null for smoke (no endpoint to count against) — carried as
-- null, never fabricated. Counts use the *_count convention (matches dim_corpus). embed_model
-- is sourced here (vector.embed_model) — its star home is dim_corpus (ADR-003/004), since the
-- corpus_build_id fingerprint hashes it, so it's FD on the corpus, not the retriever condition.
with src as (
    select corpus_build_id, payload, loaded_at
    from {{ source('raw', 'corpus_profile') }}
)
select
    corpus_build_id,
    payload ->> 'scale'                              as corpus_scale,
    (payload ->> 'measured_at')::timestamptz         as corpus_measured_at,
    -- graph side (null on smoke)
    (payload #>> '{graph,triples}')::bigint          as triple_count,
    (payload #>> '{graph,nodes}')::bigint            as node_count,
    (payload #>> '{graph,edges}')::bigint            as edge_count,
    (payload #>> '{graph,ttl_bytes}')::bigint        as ttl_bytes,
    payload #>> '{graph,ttl_sha256}'                 as ttl_sha256,
    -- vector side (always present)
    (payload #>> '{vector,n_abstracts}')::bigint     as paper_count,
    (payload #>> '{vector,n_chunks}')::bigint         as chunk_count,
    (payload #>> '{vector,n_words}')::bigint          as word_count,
    (payload #>> '{vector,chunk_size}')::int          as chunk_size,
    (payload #>> '{vector,chunk_overlap}')::int       as chunk_overlap,
    (payload #>> '{vector,store_bytes}')::bigint      as store_bytes,
    payload #>> '{vector,embed_model}'               as embed_model,
    loaded_at
from src
