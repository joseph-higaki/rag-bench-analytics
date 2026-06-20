-- The fact. Grain: one scored answer = run x question. FKs are computed with the SAME
-- surrogate_key() column lists as the dims, so they join exactly. Sparse traversal
-- measures (null where a mechanism doesn't produce them) are expected and acceptable.
-- sparql / sources / endpoint text stays in raw/staging provenance — dropped from here.
select
    -- degenerate keys (natural, kept for traceability and as the grain)
    {{ surrogate_key(['run_id', 'question_id']) }}  as scored_answer_sk,
    run_id,
    question_id,
    writer_model,

    -- foreign keys to the conformed dimensions
    {{ surrogate_key(['run_id']) }}                                                                   as run_sk,
    {{ surrogate_key(['question_id']) }}                                                              as question_sk,
    {{ surrogate_key(['generator_provider', 'generator_model_resolved', 'generator_temperature']) }} as generator_sk,
    {{ surrogate_key(['retriever', 'mechanism', 'neighborhood_hops']) }}                              as retriever_cond_sk,
    {{ surrogate_key(['scoring']) }}                                                                  as judge_sk,
    {{ surrogate_key(['corpus_build_id']) }}                                                          as corpus_sk,

    -- outcome measures
    score,
    passed,
    judged,
    is_error,
    cast(case when passed then 1 else 0 end as integer)         as is_pass,

    -- token + latency measures
    input_tokens,
    output_tokens,
    cache_read_input_tokens,
    cache_creation_input_tokens,
    cast(total_tokens as bigint)                    as total_tokens,
    context_tokens_proxy,
    num_sources,
    retrieval_latency_ms,
    generation_latency_ms,
    cast(total_latency_ms as numeric)               as total_latency_ms,

    -- exploded traversal measures
    neighborhood_hops,
    num_triples,
    num_linked,
    top_k,
    num_chunks,
    writer_temperature,
    writer_input_tokens,
    writer_output_tokens,
    cast(writer_tokens as bigint)                   as writer_tokens,
    sparql_valid,
    sparql_num_rows,

    -- cost measures (USD)
    cast(generator_cost_usd as numeric)             as generator_cost_usd,
    cast(writer_cost_usd as numeric)                as writer_cost_usd,
    cast(coalesce(generator_cost_usd, 0) + coalesce(writer_cost_usd, 0) as numeric) as total_cost_usd
from {{ ref('int_scored_answers_enriched') }}
