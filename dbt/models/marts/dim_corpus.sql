-- Corpus dimension. Grain: one row per corpus_build_id. corpus_build_id looks like
-- 'full-2c102cb0' => scale + short sha, parsed out here. node/edge counts and ttl_sha256
-- come from the corpus-profile JSON (referenced by the run manifest) — not present in
-- the current run fixtures, so left nullable rather than fabricated.
with observed as (
    select distinct corpus_build_id
    from {{ ref('stg_runs') }}
)
select
    {{ surrogate_key(['corpus_build_id']) }}        as corpus_sk,
    corpus_build_id,
    split_part(coalesce(corpus_build_id, ''), '-', 1) as corpus_scale,
    split_part(coalesce(corpus_build_id, ''), '-', 2) as corpus_sha,
    cast(null as bigint)                            as node_count,
    cast(null as bigint)                            as edge_count,
    cast(null as varchar)                           as ttl_sha256
from observed
