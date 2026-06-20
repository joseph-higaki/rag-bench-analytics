-- Run dimension. Grain: one row per run_id. Descriptive run-level attributes only;
-- the run's generator/corpus/judge are reached via the fact's other FKs (star, not
-- snowflake).
select
    {{ surrogate_key(['run_id']) }}     as run_sk,
    run_id,
    run_ts,
    judge,
    harness_version,
    system_prompt_sha256
from {{ ref('stg_runs') }}
