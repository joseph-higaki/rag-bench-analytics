-- The fact. Grain: one scored answer = run x question. Surrogate-keys-only (ADR-003):
-- the PK + FKs are md5 surrogates computed with the SAME surrogate_key() column lists as
-- the dims, so they join exactly; natural keys (run_id/question_id) and condition knobs
-- (top_k, neighborhood_hops, writer_model/_temperature) live in their dimensions, NOT here.
-- Sparse traversal measures (null where a mechanism doesn't produce them) are expected.
-- sparql / sources / endpoint text stays in raw/staging provenance — dropped from here.
select
    -- surrogate primary key (hashed from the natural grain, which is not itself carried)
    {{ surrogate_key(['run_id', 'question_id']) }}  as scored_answer_sk,

    -- foreign keys to the conformed dimensions (each list matches its dim exactly)
    {{ surrogate_key(['run_id']) }}                                                            as run_sk,
    {{ surrogate_key(['question_id']) }}                                                       as question_sk,
    {{ surrogate_key(['generator_provider', 'generator_model_id', 'generator_temperature']) }} as generator_sk,
    {{ surrogate_key(['retriever', 'mechanism', 'neighborhood_hops', 'top_k']) }}              as retriever_cond_sk,
    {{ surrogate_key(['writer_model', 'writer_temperature']) }}                                as writer_sk,
    {{ surrogate_key(['scoring']) }}                                                           as scoring_sk,
    {{ surrogate_key(['corpus_build_id']) }}                                                   as corpus_sk,

    -- pricing FKs -> dim_token_pricing. CARRIED from int (the gp/wp cost join), not rebuilt with
    -- surrogate_key(): they must be NULL when the model is unpriced (no dim row) so the relationship
    -- holds and a null FK lines up with a null cost. Rehashing here would point at a nonexistent row.
    cast(generator_pricing_sk as text)              as generator_pricing_sk,
    cast(writer_pricing_sk as text)                 as writer_pricing_sk,

    -- outcome measures
    score,
    is_passed,
    is_judged,
    is_error,

    -- generator token + latency measures
    generator_input_tokens,
    generator_output_tokens,
    generator_cache_read_tokens,
    generator_cache_creation_tokens,
    cast(generator_total_tokens as bigint)          as generator_total_tokens,
    context_tokens_proxy,
    num_sources,
    retrieval_latency_ms,
    generation_latency_ms,
    cast(total_latency_ms as numeric)               as total_latency_ms,

    -- exploded traversal measures (mechanism-prefixed; knobs live in dim_retriever_cond)
    neighborhood_num_triples,
    neighborhood_num_linked,
    dense_num_chunks,
    writer_input_tokens,
    writer_output_tokens,
    cast(writer_total_tokens as bigint)             as writer_total_tokens,
    is_sparql_valid,
    sparql_num_rows,

    -- cost measures (USD)
    cast(generator_cost_usd as numeric)             as generator_cost_usd,
    cast(writer_cost_usd as numeric)                as writer_cost_usd,
    cast(coalesce(generator_cost_usd, 0) + coalesce(writer_cost_usd, 0) as numeric) as total_cost_usd
from {{ ref('int_scored_answers_enriched') }}
