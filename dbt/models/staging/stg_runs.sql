-- Flatten the run manifest payload (1:1 with raw.run_manifest). Cast + rename only;
-- no joins, no business logic. Unknown keys in payload are simply not selected here
-- (schema-tolerant by construction).
with src as (
    select run_id, source_uri, loaded_at, payload
    from {{ source('raw', 'run_manifest') }}
)
select
    run_id,
    (payload ->> 'timestamp')::timestamptz                  as run_ts,
    payload ->> 'retriever'                                 as retriever,
    payload ->> 'generator_provider'                        as generator_provider,
    payload ->> 'generator_model'                           as generator_model,
    payload ->> 'generator_model_resolved'                  as generator_model_resolved,
    (payload ->> 'generator_temperature')::numeric          as generator_temperature,
    payload ->> 'judge'                                     as judge,
    payload ->> 'corpus_build_id'                           as corpus_build_id,
    payload ->> 'harness_version'                           as harness_version,
    (payload ->> 'num_questions')::int                      as num_questions,
    payload ->> 'system_prompt_sha256'                      as system_prompt_sha256,
    source_uri,
    loaded_at
from src
