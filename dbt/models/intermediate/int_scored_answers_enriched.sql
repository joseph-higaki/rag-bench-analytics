-- Compose the per-answer grain with question attributes, run attributes, the exploded
-- traversal measures, and COST. Cost joins the conformed pricing relation int_model_pricing
-- (the swap point) on model_resolved (USD per 1M tokens). Two cost components:
--   generator_cost_usd : the answering LLM (input + output + cache read + cache write)
--   writer_cost_usd    : the sparqlgen writer LLM (input + output), null for other mechanisms
-- A null model_resolved (older runs) or an unpriced model yields null cost — acceptable
-- sparsity, surfaced as such in the dashboard rather than silently coerced to 0.
with answers as (
    select * from {{ ref('stg_scored_answers') }}
),
traversal as (
    select * from {{ ref('stg_traversal') }}
),
questions as (
    select * from {{ ref('stg_questions') }}
),
runs as (
    select run_id, run_ts, judge_model, corpus_build_id, harness_version, generator_system_prompt_sha256
    from {{ ref('stg_runs') }}
),
gen_price as (
    select * from {{ ref('int_model_pricing') }}
),
writer_price as (
    select * from {{ ref('int_model_pricing') }}
)
select
    a.run_id,
    a.question_id,

    -- run attributes (degenerate / dimension feeds)
    r.run_ts,
    r.judge_model,
    r.corpus_build_id,
    r.harness_version,
    r.generator_system_prompt_sha256,

    -- generator attributes (conformed identity + family rollup; raw _resolved is used
    -- only by the cost join below, so it need not surface here)
    a.generator_provider,
    a.generator_model_id,
    a.generator_model_family,
    a.generator_temperature,

    -- retriever condition attributes
    a.retriever,
    t.mechanism,
    t.writer_model,
    t.writer_model_family,
    t.embed_model,
    t.endpoint,

    -- question attributes
    a.scoring,
    q.type_id,
    q.template_id,
    q.question_hop_count,
    q.num_seed_entities,

    -- outcome measures
    a.score,
    a.is_passed,
    a.is_judged,
    a.verdict,
    a.error,
    (a.error is not null)                           as is_error,

    -- token + latency measures
    a.generator_input_tokens,
    a.generator_output_tokens,
    a.generator_cache_read_tokens,
    a.generator_cache_creation_tokens,
    coalesce(a.generator_input_tokens, 0) + coalesce(a.generator_output_tokens, 0) as generator_total_tokens,
    a.context_tokens_proxy,
    a.num_sources,
    a.retrieval_latency_ms,
    a.generation_latency_ms,
    coalesce(a.retrieval_latency_ms, 0) + coalesce(a.generation_latency_ms, 0) as total_latency_ms,

    -- exploded traversal measures (neighborhood_hops/top_k are knobs -> dim_retriever_cond;
    -- writer_model/_temperature -> dim_writer; carried here only to build those dims/keys)
    t.neighborhood_hops,
    t.neighborhood_num_triples,
    t.neighborhood_num_linked,
    t.top_k,
    t.dense_num_chunks,
    t.writer_temperature,
    t.writer_input_tokens,
    t.writer_output_tokens,
    coalesce(t.writer_input_tokens, 0) + coalesce(t.writer_output_tokens, 0) as writer_total_tokens,
    t.is_sparql_valid,
    t.sparql_num_rows,

    -- pricing FKs: carried from the gp/wp cost join, NULL exactly when the model is unpriced
    -- (no dim row) so null FK == null cost (ADR-003 surrogate FK; never rehashed in the fact).
    gp.pricing_sk                                   as generator_pricing_sk,
    wp.pricing_sk                                   as writer_pricing_sk,

    -- COST (per 1M tokens -> divide by 1e6)
    (
        coalesce(a.generator_input_tokens, 0)            * gp.input_usd_per_mtok
      + coalesce(a.generator_output_tokens, 0)           * gp.output_usd_per_mtok
      + coalesce(a.generator_cache_read_tokens, 0)       * gp.cache_read_usd_per_mtok
      + coalesce(a.generator_cache_creation_tokens, 0)   * gp.cache_write_usd_per_mtok
    ) / 1e6                                          as generator_cost_usd,
    (
        coalesce(t.writer_input_tokens, 0)  * wp.input_usd_per_mtok
      + coalesce(t.writer_output_tokens, 0) * wp.output_usd_per_mtok
    ) / 1e6                                          as writer_cost_usd
from answers a
left join traversal t   on a.run_id = t.run_id and a.question_id = t.question_id
left join questions q   on a.question_id = q.question_id
left join runs r        on a.run_id = r.run_id
left join gen_price gp   on a.generator_model_resolved = gp.model_resolved
left join writer_price wp on t.writer_model = wp.model_resolved
