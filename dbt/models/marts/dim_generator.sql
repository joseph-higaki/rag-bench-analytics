-- Generator dimension. Grain: distinct (provider, model_resolved, temperature) actually
-- observed across answers. is_local flags Ollama (zero-cost, on-prem). The fixed
-- generator is the controlled variable per run — the compared variable is the retriever
-- (CLAUDE.md ground-truth semantics), so this dim is small.
select
    {{ surrogate_key(['generator_provider', 'generator_model_resolved', 'generator_temperature']) }} as generator_sk,
    generator_provider,
    max(generator_model)                    as generator_model,
    generator_model_resolved,
    generator_temperature,
    (generator_provider = 'ollama')         as is_local
from {{ ref('int_scored_answers_enriched') }}
group by generator_provider, generator_model_resolved, generator_temperature
