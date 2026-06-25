-- Generator dimension. Grain: distinct (provider, model_id, temperature) observed, where
-- model_id = coalesce(model_resolved, model) — conformed in staging so a run that logged
-- only the bare alias collapses into the same row as its dated snapshot. generator_model_family
-- is the snapshot-stripped rollup label (via the model_family macro). is_local flags Ollama
-- (zero-cost, on-prem). The fixed generator is the controlled variable per run — the compared
-- variable is the retriever (CLAUDE.md ground-truth semantics), so this dim is small.
select
    {{ surrogate_key(['generator_provider', 'generator_model_id', 'generator_temperature']) }} as generator_sk,
    generator_provider,
    generator_model_id,
    generator_model_family,
    generator_temperature,
    (generator_provider = 'ollama')         as is_local
from {{ ref('int_scored_answers_enriched') }}
group by generator_provider, generator_model_id, generator_model_family, generator_temperature
