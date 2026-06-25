-- Flatten the per-answer record (grain: run x question). Typed casts of the stable
-- top-level fields. NOTE on nullability (validated against the real fixtures):
--   * score / passed are NULL on error records (158 of 3461; judged = false there).
--   * generator_model_resolved is NULL on older runs (~14%); cost is null there.
--   * cache_* and generator_temperature are absent on some runs; ->> yields NULL.
-- The polymorphic traversal_info is NOT flattened here — see stg_traversal.
with src as (
    select run_id, question_id, source_uri, loaded_at, payload
    from {{ source('raw', 'scored_answer') }}
)
select
    run_id,
    question_id,
    payload ->> 'retriever'                                 as retriever,
    payload ->> 'scoring'                                   as scoring,
    payload ->> 'generator_provider'                        as generator_provider,
    payload ->> 'generator_model'                           as generator_model,
    payload ->> 'generator_model_resolved'                  as generator_model_resolved,
    -- conformed generator identity: prefer the dated snapshot, fall back to the bare alias.
    -- Keyed by dim_generator + the fact's generator_sk; the bare _model is provenance only.
    coalesce(payload ->> 'generator_model_resolved',
             payload ->> 'generator_model')                 as generator_model_id,
    {{ model_family("coalesce(payload ->> 'generator_model_resolved', payload ->> 'generator_model')") }}
                                                            as generator_model_family,
    (payload ->> 'generator_temperature')::numeric          as generator_temperature,

    -- outcome measures
    (payload ->> 'score')::numeric                          as score,
    (payload ->> 'passed')::boolean                         as is_passed,
    (payload ->> 'judged')::boolean                         as is_judged,
    payload ->> 'verdict'                                   as verdict,
    nullif(payload ->> 'error', '')                         as error,

    -- generator token + latency measures
    (payload ->> 'input_tokens')::bigint                    as generator_input_tokens,
    (payload ->> 'output_tokens')::bigint                   as generator_output_tokens,
    (payload ->> 'cache_read_input_tokens')::bigint         as generator_cache_read_tokens,
    (payload ->> 'cache_creation_input_tokens')::bigint     as generator_cache_creation_tokens,
    (payload ->> 'context_tokens_proxy')::bigint            as context_tokens_proxy,
    (payload ->> 'num_sources')::int                        as num_sources,
    (payload ->> 'retrieval_latency_ms')::numeric           as retrieval_latency_ms,
    (payload ->> 'generation_latency_ms')::numeric          as generation_latency_ms,

    source_uri,
    loaded_at
from src
